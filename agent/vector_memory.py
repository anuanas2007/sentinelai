"""
Long-term semantic incident memory -- the actual point of this module
is the opposite of redis_store.py's, not a duplicate of it. Redis
answers "how many times has this exact event type happened in the
last 24 hours" (exact match, short window, expires on purpose).
This answers "has something LIKE this happened before, ever, even if
it was a different event type" (similarity match, no expiry -- the
whole idea is accumulating knowledge over time, not forgetting it).

Embeddings are computed locally (sentence-transformers), not via the
OpenAI API -- deliberately zero marginal cost per incident stored,
independent of the existing AI-call cost-control work in
error_detector.py. Chroma itself only stores vectors + metadata and
does the similarity search; it never computes an embedding itself
here, so it doesn't need (and isn't given) any embedding function of
its own -- this module always supplies a precomputed embedding.

What's embedded vs. what's stored as the payload are different
things, deliberately: the *incident summary* (the input to the
investigator) is what gets embedded, so a NEW incident is matched
against what PAST incidents looked like. The *diagnosis* (the
investigator's output) is what gets stored as the retrievable
document -- that's the actual useful payload to surface, not the
summary that produced it.
"""
import os
import time
import chromadb
from sentence_transformers import SentenceTransformer

CHROMA_HOST = os.environ.get("CHROMA_HOST", "localhost")
CHROMA_PORT = int(os.environ.get("CHROMA_PORT", "8000"))
COLLECTION_NAME = "incident_diagnoses"
EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"

_client = None
_collection = None
_embedding_model = None


def _get_collection():
    global _client, _collection
    if _collection is None:
        _client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
        # No embedding_function set -- this module always supplies its
        # own precomputed embeddings (see module docstring), so Chroma
        # is never asked to compute one itself.
        _collection = _client.get_or_create_collection(name=COLLECTION_NAME)
    return _collection


def _get_embedding_model() -> SentenceTransformer:
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _embedding_model


def store_incident(event_name: str, incident_summary: str, diagnosis: str, fix_proposal: str) -> None:
    """
    Called once per completed investigation, after both the
    investigator and fix-proposal agents have finished -- not on every
    detected incident, only ones that actually got AI analysis (see
    ai_engine.py). Stores both, not just the diagnosis -- a future
    similar incident benefits from "here's what was wrong AND what was
    suggested last time," not just the diagnosis alone.
    """
    collection = _get_collection()
    model = _get_embedding_model()
    embedding = model.encode(incident_summary).tolist()
    incident_id = f"{event_name}:{time.time()}"

    collection.add(
        ids=[incident_id],
        embeddings=[embedding],
        documents=[diagnosis],
        metadatas=[{
            "event": event_name,
            "incident_summary": incident_summary,
            "fix_proposal": fix_proposal,
            "timestamp": time.time(),
        }],
    )


def query_similar(incident_summary: str, n_results: int = 3) -> list[dict]:
    """
    Returns up to n_results past diagnoses whose incident summaries are
    semantically closest to the one given -- regardless of whether they
    were the same event type. Empty list if nothing's been stored yet,
    not an error -- a cold start is a normal, expected state.
    """
    collection = _get_collection()
    model = _get_embedding_model()
    embedding = model.encode(incident_summary).tolist()

    results = collection.query(query_embeddings=[embedding], n_results=n_results)

    matches = []
    documents = results.get("documents") or [[]]
    metadatas = results.get("metadatas") or [[]]
    distances = results.get("distances") or [[]]
    for doc, meta, dist in zip(documents[0], metadatas[0], distances[0]):
        matches.append({
            "event": meta.get("event"),
            "diagnosis": doc,
            "fix_proposal": meta.get("fix_proposal"),
            "distance": dist,
        })
    return matches
