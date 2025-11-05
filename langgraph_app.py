import os, json, re, tempfile, zipfile
from typing import TypedDict, Optional, Dict, Any
from dotenv import load_dotenv
import streamlit as st 
try:
    from langgraph.graph import StateGraph, START, END
except Exception:
    try:
        from langgraph.graph import Graph as StateGraph
    except Exception:
        raise

from openai import AzureOpenAI

from utils.nuget_helper import detect_private_feeds, generate_jwt_token_for_feed, get_latest_nuget_version_for_feed, collect_csproj_files, read_text
from utils.file_utils import extract_diffs_from_markdown
# --- Load Azure credentials from Streamlit Secrets or env vars ---
AZURE_API_KEY = st.secrets.get("AZURE_OPENAI_API_KEY", os.getenv("AZURE_OPENAI_API_KEY"))
AZURE_ENDPOINT = st.secrets.get("AZURE_OPENAI_ENDPOINT", os.getenv("AZURE_OPENAI_ENDPOINT"))
AZURE_DEPLOYMENT = st.secrets.get("AZURE_OPENAI_MODEL_DEPLOYMENT_NAME", os.getenv("AZURE_OPENAI_MODEL_DEPLOYMENT_NAME"))

if not (AZURE_API_KEY and AZURE_ENDPOINT and AZURE_DEPLOYMENT):
    st.error("❌ Missing Azure OpenAI credentials. Please add them to Streamlit Secrets.")
    st.stop()
    
client = AzureOpenAI(api_key=AZURE_API_KEY, api_version=os.getenv("AZURE_OPENAI_API_VERSION"), azure_endpoint=AZURE_ENDPOINT)
deployment = AZURE_DEPLOYMENT

# State schema
class UpgradeState(TypedDict, total=False):
    uploaded_file_path: str
    user_feed_url: str
    private_feeds: list
    feed_tokens: dict
    csproj_paths: list
    package_report: dict
    analysis_report: str
    upgrade_preview: str
    csproj_updates: dict
    target_version: str

# Create StateGraph (langgraph >=0.0.42 style)
try:
    graph = StateGraph(UpgradeState)
except TypeError:
    # older versions
    graph = StateGraph()

# --- Node implementations ---
def upload_node(state: UpgradeState) -> UpgradeState:
    # state contains uploaded_file_path already (extracted by app)
    root = state["uploaded_file_path"]
    state["csproj_paths"] = collect_csproj_files(root)
    return state

def detect_feeds_node(state: UpgradeState) -> UpgradeState:
    root = state["uploaded_file_path"]
    # if user provided feed, prefer that
    feeds = []
    if state.get("user_feed_url"):
        feeds = [state["user_feed_url"]]
    else:
        feeds = detect_private_feeds(root)
    state["private_feeds"] = feeds
    return state

def generate_jwt_node(state: UpgradeState) -> UpgradeState:
    tokens = {}
    for feed in state.get("private_feeds", []):
        # generate ephemeral jwt; secret can be stored or generated per feed
        jwt = generate_jwt_token_for_feed(feed)
        tokens[feed] = jwt
    state["feed_tokens"] = tokens
    return state

def scan_packages_node(state: UpgradeState) -> UpgradeState:
    root = state["uploaded_file_path"]
    csprojs = state.get("csproj_paths", []) or collect_csproj_files(root)
    pkgs = {}
    for p in csprojs:
        text = read_text(p)
        for name, ver in re.findall(r'<PackageReference\s+Include="([^"]+)"\s+Version="([^"]+)"', text):
            # fetch latest using private feed token if present
            feed = (state.get("private_feeds") or [None])[0]  # single feed support
            token = state.get("feed_tokens", {}).get(feed)
            latest = get_latest_nuget_version_for_feed(name, feed, token)
            pkgs.setdefault(name, {"current": ver})
            pkgs[name]["latest"] = latest
    state["package_report"] = pkgs
    return state

def analyze_ai_node(state: UpgradeState) -> UpgradeState:
    # Use your exact prompt (verbatim) — include context
    user_prompt = """1. Private NuGet Feed Support

Implement support for private NuGet feeds

Enable the Agent to download and update NuGet packages from these feeds

2. Breaking Changes Detection and Resolution

Enhance agent to identify breaking changes in the latest versions of NuGet packages

Include a report detailing these breaking changes

Provide step-by-step resolution instructions

Where possible, automate the resolution process

3. Deprecated Package Handling

Enhance agent to detect and report deprecated NuGet packages

Include information on alternative packages recommended by the original package owner

If feasible, automate the process of switching to the recommended alternative package

4. Security Vulnerability Reporting

Enhance agent to identify and showcase security vulnerabilities in the current package

setup

In the report, list which packages can resolve these vulnerabilities"""

    root = state["uploaded_file_path"]
    csprojs_text = ""
    for p in state.get("csproj_paths", []):
        rel = os.path.relpath(p, root)
        csprojs_text += f"// FILE: {rel}\n" + read_text(p) + "\n\n"
    package_report_json = json.dumps(state.get("package_report", {}), indent=2)
    prompt = f"""
You are an expert .NET upgrade assistant.

User instructions (DO NOT CHANGE):
{user_prompt}

Context:
- Target .NET version: {state.get('target_version')}
- Private feeds: {json.dumps(state.get('private_feeds', []))}
- Package report (current -> latest):
{package_report_json}

Project .csproj contents:
{csprojs_text}

Please produce a structured Markdown report covering:
- Executive summary
- Private feed handling & commands
- Breaking changes detection & step-by-step resolution
- Deprecated package handling & suggested alternatives
- Security vulnerability reporting and which packages resolve them
- Final ordered automation plan (commands and csproj patches)
"""
    resp = client.chat.completions.create(model=deployment, messages=[{"role":"user","content":prompt}], max_tokens=3000)
    state["analysis_report"] = resp.choices[0].message.content
    return state

def upgrade_ai_node(state: UpgradeState) -> UpgradeState:
    root = state["uploaded_file_path"]
    csprojs_text = ""
    for p in state.get("csproj_paths", []):
        rel = os.path.relpath(p, root)
        csprojs_text += f"// FILE: {rel}\n" + read_text(p) + "\n\n"

    prompt = f"""
Using the analysis and rules, produce UPDATED .csproj XML for each file to target {state.get('target_version')}.
- Do NOT auto-change private/third-party package versions; mark them 'Manual Review Required (Private Feed)'.
- For public packages, update to latest stable when safe.
- For deprecated packages, prefer recommended alternatives and include csproj patch.
Return output as machine-parseable blocks:

--FILE: relative/path/to/Proj.csproj --
<full updated csproj xml>
--END FILE--
"""
    resp = client.chat.completions.create(model=deployment, messages=[{"role":"user","content":prompt + "\n\n" + csprojs_text}], max_tokens=3500)
    preview = resp.choices[0].message.content
    state["upgrade_preview"] = preview

    # Parse preview into csproj_updates mapping
    csproj_updates = {}
    for m in re.finditer(r"--FILE:\s*(.*?)\s*--\n(.*?)--END FILE--", preview, re.S):
        rel = m.group(1).strip()
        xml = m.group(2).strip()
        csproj_updates[rel] = xml
    state["csproj_updates"] = csproj_updates
    return state

def final_node(state: UpgradeState) -> UpgradeState:
    # nothing special; pass through
    return state

# --- register nodes and edges ---
try:
    graph.add_node("upload", upload_node)
    graph.add_node("detect_feeds", detect_feeds_node)
    graph.add_node("gen_jwt", generate_jwt_node)
    graph.add_node("scan_pkgs", scan_packages_node)
    graph.add_node("analyze_ai", analyze_ai_node)
    graph.add_node("upgrade_ai", upgrade_ai_node)
    graph.add_node("final", final_node)
    graph.set_entry_point("upload")
    graph.add_edge("upload", "detect_feeds")
    graph.add_edge("detect_feeds", "gen_jwt")
    graph.add_edge("gen_jwt", "scan_pkgs")
    graph.add_edge("scan_pkgs", "analyze_ai")
    graph.add_edge("analyze_ai", "upgrade_ai")
    graph.add_edge("upgrade_ai", "final")
except Exception:
    # if graph API differs, you might need to adapt to your langgraph version
    pass

# helper to run the graph from Streamlit app
def build_graph():
    return graph

def run_graph_invoke(graph_obj, initial_state: dict) -> dict:
    # Graph invoke APIs vary; try common ones
    try:
        result = graph_obj.invoke(initial_state)
        return result
    except Exception:
        # fallback: run nodes sequentially
        state = initial_state.copy()
        state = upload_node(state)
        state = detect_feeds_node(state)
        state = generate_jwt_node(state)
        state = scan_packages_node(state)
        state = analyze_ai_node(state)
        state = upgrade_ai_node(state)
        state = final_node(state)
        return state
