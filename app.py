import os
import zipfile
import tempfile
import shutil
import streamlit as st
st.set_page_config(page_title="AI .NET Upgrader (LangGraph)", layout="wide")

from langgraph_app import build_graph, run_graph_invoke
from utils.file_utils import save_uploaded_zip, create_upgraded_zip, extract_diffs_from_markdown



# --- Check Azure OpenAI credentials ---
def check_azure_connection():
    from openai import AzureOpenAI
    try:
        # Load from Streamlit secrets or env vars
        api_key = st.secrets.get("AZURE_OPENAI_API_KEY", os.getenv("AZURE_OPENAI_API_KEY"))
        endpoint = st.secrets.get("AZURE_OPENAI_ENDPOINT", os.getenv("AZURE_OPENAI_ENDPOINT"))
        deployment = st.secrets.get("AZURE_OPENAI_MODEL_DEPLOYMENT_NAME", os.getenv("AZURE_OPENAI_MODEL_DEPLOYMENT_NAME"))

        if not (api_key and endpoint and deployment):
            st.warning("⚠️ Azure OpenAI credentials not set. Please add them in Streamlit Secrets.")
            return False

        # Test connection
        client = AzureOpenAI(api_key=api_key, azure_endpoint=endpoint, api_version="2024-08-01-preview")
        client.models.list()  # simple call
        st.success(f"🔒 Connected to Azure OpenAI — Model: `{deployment}`")
        return True
    except Exception as e:
        st.error(f"❌ Azure connection failed: {e}")
        return False


# --- Run connection check before rest of UI loads ---
check_azure_connection()

st.title("AI .NET Upgrader — LangGraph Edition")

st.sidebar.header("Settings")
target_version = st.sidebar.selectbox("Target .NET version", ["net6.0", "net7.0", "net8.0", "net9.0-preview"])
st.sidebar.caption("Provide a single private NuGet feed URL (optional)")

uploaded = st.file_uploader("Upload your .NET project (.zip)", type=["zip"])
private_feed = st.text_input("Private NuGet Feed URL (single)", value="")

graph = build_graph()

if uploaded:
    project_root = save_uploaded_zip(uploaded)
    st.success(f"Project uploaded and extracted.")
    if st.button("🚀 Start Analysis"):
        with st.spinner("Running LangGraph pipeline..."):
            state = {"uploaded_file_path": project_root, "target_version": target_version}
            if private_feed: state["user_feed_url"] = private_feed
            result = run_graph_invoke(graph, state)

        st.success("✅ Analysis complete.")

        with st.expander("🔗 Detected Private Feeds", expanded=False): st.json(result.get("private_feeds", []))
        with st.expander("📦 Package Report", expanded=False): st.json(result.get("package_report", {}))
        with st.expander("🧠 AI Analysis Report", expanded=False): st.markdown(result.get("analysis_report", "_No report returned_"))
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

        if st.button("📥 Download AI-Upgraded Project (.zip)"):
            csproj_updates = result.get("csproj_updates", {})
            zip_out = create_upgraded_zip(project_root, csproj_updates, target_version)
            with open(zip_out, "rb") as fh:
                st.download_button("Download Now", fh, file_name=os.path.basename(zip_out), mime="application/zip")

    if st.button("🧹 Clear Temp Files"):
        try: shutil.rmtree(project_root); st.success("Temporary files cleared.")
        except Exception as e: st.warning(f"Could not clear temp files: {e}")
