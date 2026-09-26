"""Checks every external dependency with one tiny request each. Run before a demo:

    python check_setup.py

Cost: ~2 YouTube quota units, 1 Tavily credit, 3 free Twitch calls, 2 Instagram Graph calls, 1 tiny Groq call, 1 tiny OpenAI vision call.
"""
import os
import sys

import requests

import config

OK, FAIL, WARN = "✅", "❌", "⚠️ "
results = []


def report(name, status, msg):
    results.append(status)
    print(f"{status} {name:<22} {msg}")


def check_env():
    for var, required in [("YOUTUBE_API_KEY", True), ("GROQ_API_KEY", True), ("TAVILY_API_KEY", False),
                          ("OPENAI_API_KEY", False), ("GEMINI_API_KEY", False),
                          ("TWITCH_CLIENT_ID", False), ("TWITCH_CLIENT_SECRET", False),
                          ("INSTAGRAM_ACCESS_TOKEN", False)]:
        if os.getenv(var):
            report(var, OK, "set")
        else:
            report(var, FAIL if required else WARN, "missing" + ("" if required else " (optional)"))


def check_youtube():
    try:
        r = requests.get("https://www.googleapis.com/youtube/v3/videos", timeout=20, params={
            "part": "statistics", "id": "dQw4w9WgXcQ", "key": config.YOUTUBE_API_KEY}).json()
        if "error" in r:
            report("YouTube Data API", FAIL, r["error"].get("message", "")[:120])
        else:
            report("YouTube Data API", OK, "videos.list works")
    except Exception as e:
        report("YouTube Data API", FAIL, str(e)[:120])


def _models(base, key):
    r = requests.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"}, timeout=20)
    r.raise_for_status()
    return {m["id"] for m in r.json()["data"]}


def check_chain(label, base, key, chain, provider):
    if not key:
        return
    try:
        available = _models(base, key)
    except Exception as e:
        report(label, FAIL, f"cannot list models: {str(e)[:100]}")
        return
    wanted = [m.split("/", 1)[1] for m in chain if m.startswith(provider + "/")]
    missing = [m for m in wanted if m not in available]
    if len(missing) == len(wanted):
        report(label, FAIL, f"none of the configured models exist: {wanted}")
    elif missing:
        report(label, WARN, f"reachable; not available (will be skipped): {missing}")
    else:
        report(label, OK, f"reachable; models available: {wanted}")


def check_llm_calls():
    from pydantic import BaseModel
    from llm import LLMClient

    class Pong(BaseModel):
        answer: str

    if config.GROQ_API_KEY:
        groq_only = [m for m in config.LLM_MODELS if m.startswith("groq/")]
        try:
            c = LLMClient(groq_only)
            out = c.extract(Pong, "Reply with JSON.", f"Say 'pong' (check {os.getpid()})", deadline_s=60)
            report("Groq structured call", OK, f"{out.answer!r} via {list(c.calls_by_model)[-1]}")
        except Exception as e:
            report("Groq structured call", FAIL, str(e)[:160])
    if config.OPENAI_API_KEY:
        try:
            c = LLMClient(config.VLM_MODELS)
            out = c.extract(Pong, "Reply with JSON.", f"What is shown? One word. ({os.getpid()})",
                            images=["https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"], deadline_s=60)
            report("OpenAI vision call", OK, f"{out.answer!r} via {list(c.calls_by_model)[-1]}")
        except Exception as e:
            report("OpenAI vision call", FAIL, str(e)[:160])


def check_tavily():
    if not config.TAVILY_API_KEY:
        return
    import web_discovery as web
    try:
        res = web.tavily_search(f"site:tiktok.com gaming pc deutsch", max_results=3)
        report("Tavily search", OK, f"{len(res)} results, e.g. {res[0]['url'][:60] if res else '-'}")
    except Exception as e:
        report("Tavily search", FAIL, str(e)[:160])


def check_twitch():
    import twitch
    if not twitch.available():
        return
    try:
        streams = twitch.live_streams("de", [g["id"] for g in twitch.top_games(5)], pages=1)
        followers = twitch.follower_total(streams[0]["user_id"]) if streams else None
        report("Twitch Helix", OK, f"{len(streams)} live German streams in top games"
               + (f", e.g. {streams[0]['user_name']} ({followers:,} followers)" if streams and followers is not None
                  else ""))
        if streams and followers is None:
            report("Twitch followers", WARN, "follower totals unavailable with an app token → shown as '–'")
    except Exception as e:
        report("Twitch Helix", FAIL, str(e)[:160])


def check_instagram():
    import instagram
    if not instagram.available():
        return
    try:
        me = instagram.my_ig_id()
        p = instagram.profile("instagram")  # a public business account, works for any valid token
        report("Instagram Graph API", OK if p else WARN, f"your IG account id {me}; "
               + (f"@instagram has {p['followers']:,} followers" if p else "Business Discovery returned nothing"))
    except Exception as e:
        hint = " → token expired: generate a new one and extend it (60 days)" if "190" in str(e) else ""
        report("Instagram Graph API", FAIL, str(e)[:160] + hint)


def check_transcripts():
    import youtube as yt
    yt.TranscriptStatus.reset()
    text = yt.transcript("dQw4w9WgXcQ", ("en",))
    if text:
        report("Transcripts", OK, "fetched (unofficial library)")
    elif yt.TranscriptStatus.blocked:
        report("Transcripts", WARN, "blocked from this network → title+description fallback will be used")
    else:
        report("Transcripts", WARN, "not available for the test video")


if __name__ == "__main__":
    print("Creator Intelligence Engine – setup check\n")
    check_env()
    check_youtube()
    check_chain("Groq", "https://api.groq.com/openai/v1", config.GROQ_API_KEY, config.LLM_MODELS, "groq")
    check_chain("OpenAI", "https://api.openai.com/v1", config.OPENAI_API_KEY, config.VLM_MODELS, "openai")
    check_llm_calls()
    check_tavily()
    check_twitch()
    check_instagram()
    check_transcripts()
    print("\nResult:", "ready for a full run" if FAIL not in results else "fix the ❌ items first")
    sys.exit(1 if FAIL in results else 0)
