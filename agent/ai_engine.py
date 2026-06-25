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
from crewai import Agent, Task, Crew, Process
from crewai.tools import BaseTool

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
        tools=[ListSourceFilesTool(), ReadSourceFileTool()],
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
            "Using ONLY what you actually read, determine the single most "
            "likely root cause. You must reference real function/variable "
            "names that appear in the file(s) you read -- if the code "
            "doesn't actually explain the incident, say so explicitly "
            "rather than inventing plausible-sounding code that isn't "
            "there. State your confidence (0-1) and explain your "
            "reasoning in plain English, citing the specific file/"
            "function/line, and quote the relevant lines verbatim."
        ),
        expected_output=(
            "The actual relevant source code quoted verbatim with real "
            "filename(s) labeled, plus a root cause, confidence score "
            "(0-1), and plain-English explanation referencing only real "
            "names from that code -- or an explicit statement that the "
            "evidence is insufficient."
        ),
        agent=investigator_agent,
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
