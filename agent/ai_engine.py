"""
AI reasoning engine — invoked by log_collector.py's background worker
thread, never from the main log-watching loop, for incidents whose
requires_ai flag is True (see AI_WORTHY_EVENTS in error_detector.py).

Two CrewAI agents run sequentially:

  investigator_agent -> finds and reads the actual target_app source
                        (list_source_files, then read_source_file),
                        then states a root cause + confidence in the
                        same pass, grounded in what it just read
  fix_agent          -> a proposed diff + explanation for human review,
                        given investigator_agent's output as context;
                        never applies anything itself

Originally 3 agents (separate retrieval and hypothesis steps). Merged
to 2 after a real bug: the retrieval agent guessed wrong filenames,
and the hypothesis agent downstream had no way to know the retrieval
was bad, so it reasoned over incomplete context. Investigation (read
code + diagnose why) is naturally one continuous train of thought —
splitting it created a hand-off seam with no corresponding benefit.
Diagnose vs. propose-a-fix stayed split: that boundary matters for
keeping the fix agent's "never apply, only propose" framing distinct.

analyze_incident() kicks off the crew and returns the fix agent's
output as plain text — that's the only thing the caller sees.
"""
import os
from pydantic import BaseModel, Field
from crewai import Agent, Task, Crew, Process
from crewai.tools import BaseTool
import redis_store

# Mounted read-only into the container — toy-scale simplification.
# See docs/SECOND_ITERATION_ARCHITECTURE.md for why this doesn't scale
# and the planned switch to git-based retrieval.
TARGET_APP_SRC = os.environ.get("TARGET_APP_SRC", "/app/target_app_src")

LLM_MODEL = "gpt-4o-mini"  # cheap model — root cause text generation, not heavy code synthesis


class ListSourceFilesTool(BaseTool):
    name: str = "list_source_files"
    description: str = (
        "Lists the actual filenames available in the target app's source "
        "directory. Call this FIRST, before read_source_file -- do not "
        "guess filenames."
    )

    def _run(self, _: str = "") -> str:
        try:
            names = sorted(os.listdir(TARGET_APP_SRC))
        except OSError as e:
            return f"Could not list source directory: {e}"
        return "\n".join(n for n in names if n.endswith(".py")) or "No .py files found"


class ReadSourceFileTool(BaseTool):
    name: str = "read_source_file"
    description: str = (
        "Reads one file from the target app's source code by filename "
        "(e.g. 'main.py', 'db.py', 'logger.py'). Returns the file's full "
        "contents, or an error message if the file doesn't exist. Access "
        "is restricted to the target app's source directory only — "
        "subdirectory paths are stripped, so this can't read anything "
        "outside it."
    )

    def _run(self, filename: str) -> str:
        # basename() strips any directory components — prevents path
        # traversal (e.g. "../../etc/passwd") regardless of what an
        # LLM-generated input contains.
        safe_name = os.path.basename(filename)
        path = os.path.join(TARGET_APP_SRC, safe_name)
        if not os.path.isfile(path):
            return f"File not found: {safe_name}"
        with open(path, "r") as f:
            return f.read()


class _IncidentHistoryArgs(BaseModel):
    event_name: str = Field(..., description="Exact event name, e.g. 'negative_balance_detected'")
    hours: float = Field(24, description="How many hours back to look. Defaults to 24 (the max -- incidents older than 24h are not retained).")


class GetIncidentHistoryTool(BaseTool):
    name: str = "get_incident_history"
    description: str = (
        "Returns how many times this exact event type has occurred as a "
        "confirmed incident in the given time window (default 24 hours, "
        "the maximum retained), including this one. Call this only if "
        "knowing whether this is a one-off or a recurring pattern would "
        "actually help your diagnosis -- this is factual frequency data, "
        "not a hint about the cause."
    )
    args_schema: type[BaseModel] = _IncidentHistoryArgs

    def _run(self, event_name: str, hours: float = 24) -> str:
        try:
            count = redis_store.count_in_window(event_name, hours=hours)
        except Exception as e:
            return f"Could not retrieve incident history: {e}"
        return f"'{event_name}' has occurred {count} time(s) in the last {hours} hour(s), including this one."


def _stage_callback(stage_label: str):
    """
    Prints a clean, labeled block to stdout when a task finishes --
    visible in `docker compose logs sentinel-agent`, distinct from
    CrewAI's own raw verbose debug output (which stays off by default).
    """
    def callback(output):
        text = getattr(output, "raw", None) or str(output)
        print(f"\n🔎 STAGE: {stage_label}", flush=True)
        print("-" * 60)
        print(text)
        print("-" * 60, flush=True)
    return callback


def _build_crew(incident_summary: str) -> Crew:
    investigator_agent = Agent(
        role="Incident Investigator",
        goal=(
            "Find the actual relevant source code for an incident and "
            "determine the single most likely root cause, with a "
            "confidence score"
        ),
        backstory=(
            "You are a senior engineer investigating an incident. You "
            "never guess at filenames or code — you list the real files, "
            "read the ones relevant to the incident, and reason only "
            "from what you actually read. You state your confidence "
            "honestly; if the evidence is ambiguous, say so rather than "
            "overclaiming."
        ),
        tools=[ListSourceFilesTool(), ReadSourceFileTool(), GetIncidentHistoryTool()],
        llm=LLM_MODEL,
        verbose=False,
    )

    fix_agent = Agent(
        role="Fix Proposal Engineer",
        goal="Draft a concrete suggested fix for human review — never claim it has been applied",
        backstory=(
            "You propose fixes for a human to review and apply. You are "
            "not authorized to apply changes yourself. You write a small, "
            "specific code diff and a plain-English explanation of why it "
            "addresses the root cause."
        ),
        llm=LLM_MODEL,
        verbose=False,
    )

    investigator_task = Task(
        description=(
            f"An incident occurred:\n{incident_summary}\n\n"
            "First, call list_source_files to see the actual filenames "
            "available — do not guess a filename. Then read whichever "
            "file(s) are relevant to this incident using read_source_file. "
            "IMPORTANT: if the code you read calls a function defined "
            "elsewhere (e.g. db.something(...)), read that file too before "
            "concluding -- don't stop at the first file if the real "
            "mechanism lives in a function call you haven't actually seen "
            "the body of.\n\n"
            "Using ONLY what you actually read, determine the single most "
            "likely root cause. Be specific about the exact mechanism -- "
            "name the precise sequence of operations that goes wrong (e.g. "
            "'X reads value at line N, then Y writes at line M in a "
            "different function, with nothing re-checking X's read in "
            "between'), not just a restatement of the error message. You "
            "must reference real function/variable names that appear in "
            "the file(s) you read -- if the code doesn't actually explain "
            "the incident, say so explicitly rather than inventing "
            "plausible-sounding code that isn't there. State your "
            "confidence (0-1) and quote the relevant lines verbatim."
        ),
        expected_output=(
            "The actual relevant source code quoted verbatim with real "
            "filename(s) labeled (including any cross-referenced files), "
            "plus a root cause naming the precise sequence of operations "
            "that goes wrong, a confidence score (0-1), and an explanation "
            "referencing only real names from that code -- or an explicit "
            "statement that the evidence is insufficient."
        ),
        agent=investigator_agent,
        callback=_stage_callback("Investigation (file retrieval + root cause)"),
    )

    fix_task = Task(
        description=(
            "Based on the root cause hypothesis, draft a specific, "
            "minimal suggested fix as a code diff against the ACTUAL "
            "retrieved file content -- the diff's context lines must "
            "match the real file verbatim, not an invented version of "
            "it. If the hypothesis task stated the evidence was "
            "insufficient, say so here instead of fabricating a diff. "
            "Include a one-paragraph plain-English explanation. This is "
            "a PROPOSAL for a human to review — explicitly state that it "
            "has not been applied."
        ),
        expected_output=(
            "A short code diff whose unchanged context lines match the "
            "real retrieved file verbatim, and a plain-English "
            "explanation, clearly labeled as a proposal requiring human "
            "review. Or, if evidence was insufficient, an explicit "
            "statement of that instead of a fabricated diff."
        ),
        agent=fix_agent,
        context=[investigator_task],
        callback=_stage_callback("Fix proposal (human review required)"),
    )

    return Crew(
        agents=[investigator_agent, fix_agent],
        tasks=[investigator_task, fix_task],
        process=Process.sequential,
    )


def analyze_incident(incident_summary: str) -> str:
    """
    Runs the investigator -> fix-proposal crew on one incident.
    Blocking call — meant to be run from a background thread/queue
    consumer, not the main log-watching loop. Returns the final
    fix-proposal agent's output as plain text.
    """
    crew = _build_crew(incident_summary)
    result = crew.kickoff()
    return str(result)
