import os
import re
import json
import tempfile
import zipfile
import streamlit as st
from openai import AzureOpenAI
import jwt
import datetime
from dotenv import load_dotenv

# --- Load .env locally ---
load_dotenv()

# --- Azure OpenAI credentials ---
AZURE_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_DEPLOYMENT = os.getenv("AZURE_OPENAI_MODEL_DEPLOYMENT_NAME")

if not (AZURE_API_KEY and AZURE_ENDPOINT and AZURE_DEPLOYMENT):
    st.error("❌ Missing Azure OpenAI credentials. Please set them in environment variables or .env file.")
    st.stop()

client = AzureOpenAI(
    api_key=AZURE_API_KEY,
    api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01"),
    azure_endpoint=AZURE_ENDPOINT
)

# --- JWT generation for private feeds ---
def generate_jwt_token(feed_url: str) -> str:
    payload = {
        "feed": feed_url,
        "exp": datetime.datetime.utcnow() + datetime.timedelta(minutes=30),
    }
    secret = "local-secret"
    return jwt.encode(payload, secret, algorithm="HS256")

# --- Utility Functions ---
def extract_zip(uploaded_file) -> str:
    temp_dir = tempfile.mkdtemp()
    with zipfile.ZipFile(uploaded_file, "r") as zip_ref:
        zip_ref.extractall(temp_dir)
    return temp_dir

def collect_csproj_files(root: str):
    csprojs = []
    for subdir, _, files in os.walk(root):
        for f in files:
            if f.endswith(".csproj"):
                csprojs.append(os.path.join(subdir, f))
    return csprojs

def read_file(path: str):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()

def extract_diffs_from_markdown(markdown_text: str):
    """Extract before/after blocks like a Git diff."""
    diffs = []
    pattern = r"--FILE:\s*(.*?)\s*--\n(.*?)--END FILE--"
    for match in re.finditer(pattern, markdown_text, re.S):
        fname = match.group(1).strip()
        xml = match.group(2).strip()
        before = re.search(r"<before>(.*?)</before>", xml, re.S)
        after = re.search(r"<after>(.*?)</after>", xml, re.S)
        if before and after:
            diffs.append((fname, before.group(1).strip(), after.group(1).strip()))
    return diffs

# --- Streamlit Layout ---
st.set_page_config(page_title="AI .NET Upgrader", layout="wide")
st.title("🤖 AI-Powered .NET Upgrader (Simplified Final Version)")

uploaded_file = st.file_uploader("📦 Upload your .NET project (.zip)", type=["zip"])
feed_url = st.text_input("🔗 Optional: Private NuGet Feed URL", "")
target_version = st.selectbox("🎯 Target .NET Version", ["net6.0", "net7.0", "net8.0"], index=0)

if uploaded_file and st.button("🚀 Start Analysis"):
    with st.spinner("Analyzing your project..."):
        project_dir = extract_zip(uploaded_file)
        csproj_files = collect_csproj_files(project_dir)

        private_feeds = [feed_url] if feed_url else []
        feed_tokens = {}
        if feed_url:
            feed_tokens[feed_url] = generate_jwt_token(feed_url)

        # Read project files
        csproj_text = ""
        for path in csproj_files:
            rel = os.path.relpath(path, project_dir)
            csproj_text += f"// FILE: {rel}\n{read_file(path)}\n\n"

        # --- AI Analysis Prompt ---
        analysis_prompt = f"""
You are an expert .NET upgrade assistant.

Analyze this project for upgrade to {target_version}.
Include:
- Private feed handling
- Outdated NuGet packages
- Breaking changes
- Deprecated APIs and vulnerabilities
- Step-by-step upgrade recommendations

Project files:
{csproj_text}
"""
        response = client.chat.completions.create(
            model=AZURE_DEPLOYMENT,
            messages=[{"role": "user", "content": analysis_prompt}],
            max_tokens=3000,
        )
        analysis_report = response.choices[0].message.content

        # --- AI Upgrade Preview ---
        upgrade_prompt = f"""
Using the above analysis, produce updated .csproj XML for each file targeting {target_version}.
- Keep private packages unchanged (mark 'Manual Review Required')
- Update public packages to latest stable
Return output as:
--FILE: relative/path/to/project.csproj --
<before>old xml</before>
<after>updated xml</after>
--END FILE--
"""
        response_upgrade = client.chat.completions.create(
            model=AZURE_DEPLOYMENT,
            messages=[{"role": "user", "content": upgrade_prompt}],
            max_tokens=3500,
        )
        upgrade_preview = response_upgrade.choices[0].message.content

        # --- Final Display ---
        result = {
            "private_feeds": private_feeds,
            "package_report": {"Total .csproj Files": len(csproj_files)},
            "analysis_report": analysis_report,
            "upgrade_preview": upgrade_preview
        }

        st.success("✅ Analysis Completed")

        with st.expander("🔗 Detected Private Feeds", expanded=False):
            st.json(result.get("private_feeds", []))

        with st.expander("📦 Package Report", expanded=False):
            st.json(result.get("package_report", {}))

        with st.expander("🧠 AI Analysis Report", expanded=False):
            st.markdown(result.get("analysis_report", "_No report returned_"))

        with st.expander("⚙️ Upgrade Preview", expanded=False):
            st.markdown(result.get("upgrade_preview", "_No preview returned_"))
            diffs = extract_diffs_from_markdown(result.get("upgrade_preview", ""))
            if diffs:
                for fname, before, after in diffs:
                    st.subheader(fname)
                    try:
                        from streamlit_diff_viewer import diff_viewer
                        diff_viewer(before, after, language="xml")
                    except Exception:
                        st.code("Before:\n" + before, language="xml")
                        st.code("After:\n" + after, language="xml")

        # --- Download Report ---
        output_path = os.path.join(tempfile.mkdtemp(), "ai_upgrade_report.txt")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("=== AI Analysis Report ===\n\n")
            f.write(analysis_report)
            f.write("\n\n=== AI Upgrade Preview ===\n\n")
            f.write(upgrade_preview)

        with open(output_path, "rb") as f:
            st.download_button(
                "⬇️ Download Full AI Upgrade Report",
                f,
                file_name="ai_dotnet_upgrade_report.txt",
                mime="text/plain",
            )
