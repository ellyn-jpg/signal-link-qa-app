import os
import subprocess
import sys
import streamlit as st

# Force download browser files dynamically during Streamlit Cloud initialization
@st.cache_resource
def install_playwright_browsers():
    try:
        # Check if running in Streamlit Cloud environment
        if os.environ.get("STREAMLIT_RUNTIME_ENV") or not os.path.exists("/Users"):
            subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
    except Exception as e:
        st.error(f"Playwright Browser Sync Failed: {e}")

install_playwright_browsers()

# --- Rest of your imports and original scraper code below ---
import pandas as pd
from playwright.sync_api import sync_playwright
from urllib.parse import urlparse, urljoin
import time

# Set up page configuration
st.set_page_config(page_title="Link Building Verifier", page_icon="🔗", layout="wide")

st.title("🔗 Link Building Status Verifier (Playwright Powered)")
st.markdown("""
Paste your data directly from Excel or Google Sheets into the table below. 
This version uses a headless browser to accurately verify links on complex platforms like **Reddit, Quora, and Medium**.
""")

# Define the default empty dataframe structure
DEFAULT_DF = pd.DataFrame(
    [
        {"Published URL": "", "Anchor / Brand": "", "Target Page": ""}
    ]
)

# Instructions for the user
with st.sidebar:
    st.header("💡 Quick Guide")
    st.markdown("""
    1. **Copy** 3 columns from your worksheet (Published URL, Anchor, Target URL).
    2. **Click** the first cell of the table on the right.
    3. **Paste (Ctrl+V / Cmd+V)** your data.
    4. Click **Run Verification Check**.
    """)
    st.markdown("---")
    timeout = st.slider("Page Wait Timeout (seconds)", min_value=5, max_value=30, value=15, 
                        help="Time given to modern JavaScript heavy sites (like Reddit) to fully render content.")

# Data Editor interface allowing easy copy-paste
st.subheader("📋 Input Worksheet Data")
edited_df = st.data_editor(
    DEFAULT_DF,
    num_rows="dynamic",
    use_container_width=True,
    column_config={
        "Published URL": st.column_config.TextColumn("Published URL", help="e.g., https://reddit.com/r/..."),
        "Anchor / Brand": st.column_config.TextColumn("Anchor Term / Brand Name", help="e.g., BrandName"),
        "Target Page": st.column_config.TextColumn("Target Page (Optional)", help="Leave blank if it should be unlinked plain text"),
    }
)

def normalize_url(url):
    """Normalize URL to ensure fair comparison (strips trailing slashes, protocols if needed)"""
    if not url or pd.isna(url):
        return ""
    parsed = urlparse(str(url).strip().lower())
    path = parsed.path.rstrip('/')
    return f"{parsed.netloc}{path}"

def verify_link_with_browser(browser, pub_url, anchor, target_url, timeout_secs):
    """Uses a real headless browser instance to handle heavy JavaScript and bot security screens."""
    pub_url = str(pub_url).strip()
    anchor = str(anchor).strip().lower()
    
    has_target = pd.notna(target_url) and str(target_url).strip() != ""
    target_url = str(target_url).strip() if has_target else ""

    if not pub_url or not anchor:
        return "⚠️ Missing Input Data", "N/A"

    context = None
    page = None
    try:
        # Create an isolated browser tab with a realistic user agent
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        page = context.new_page()

        # Optimization: Route requests to block heavy assets (images, media, fonts) to speed up execution
        def block_aggressively(route):
            if route.request.resource_type in ["image", "media", "font", "stylesheet"]:
                route.abort()
            else:
                route.continue_()
        
        # We allow documents and scripts so Reddit/Quora applications can run and render content
        page.route("**/*", block_aggressively)

        # Navigate to target page and wait until network is mostly idle
        response = page.goto(pub_url, timeout=timeout_secs * 1000, wait_until="domcontentloaded")
        
        # If site explicitly rejects connection completely
        if not response or response.status != 200:
            status_code = response.status if response else "Unknown"
            return f"❌ Broken (HTTP {status_code})", "No"

        # Give dynamic content like React/Next.js an extra moment to complete rendering client-side links
        time.sleep(1.5)

        # 1. Grab all text content of the page to check if the brand is even mentioned
        body_text = page.locator("body").inner_text().lower()
        if anchor not in body_text:
            return "❌ Anchor text not found on page", "No"

        # 2. Extract all hyperlinks on the page via JavaScript evaluation
        # This bypasses shadow-dom issues often seen in modern web frameworks
        links = page.evaluate("""() => {
            return Array.from(document.querySelectorAll('a')).map(a => ({
                href: a.href,
                text: a.innerText || ''
            }));
        }""")

        normalized_target = normalize_url(target_url)
        anchor_is_linked_somewhere = False
        linked_to_url = ""
        link_found_with_wrong_anchor = False
        actual_anchor = ""

        for link in links:
            href = link['href']
            absolute_href = urljoin(pub_url, href)
            normalized_href = normalize_url(absolute_href)
            link_text = link['text'].strip().lower()

            # Track if our text exists inside a hyperlink structure anywhere
            if anchor in link_text:
                anchor_is_linked_somewhere = True
                linked_to_url = absolute_href

            if has_target:
                if anchor in link_text and normalized_href == normalized_target:
                    return "✅ Link Verified & Live", "Yes"
                if normalized_href == normalized_target:
                    link_found_with_wrong_anchor = True
                    actual_anchor = link['text'].strip()

        # 3. Final outcome routing logic
        if has_target:
            if link_found_with_wrong_anchor:
                return f"⚠️ Target linked, but used wrong anchor: '{actual_anchor}'", "Partial"
            if anchor_is_linked_somewhere:
                return f"⚠️ Anchor found, but links to wrong URL: {linked_to_url}", "Partial"
            return "❌ Anchor exists as plain text, but is NOT hyperlinked", "No"
        else:
            if anchor_is_linked_somewhere:
                return f"❌ Anchor found, but it IS hyperlinked to: {linked_to_url} (Expected Plain Text)", "Yes (Unwanted)"
            return "✅ Plain Text Mention Verified (No Link)", "No"

    except Exception as e:
        return f"❌ Browser Error/Timeout: {str(e)[:50]}...", "No"
    finally:
        # Always clean up the page and context tab
        if page: page.close()
        if context: context.close()


# Action button
if st.button("🚀 Run Playwright Verification Check", type="primary"):
    valid_rows = edited_df.dropna(subset=["Published URL", "Anchor / Brand"])
    valid_rows = valid_rows[(valid_rows["Published URL"] != "") & (valid_rows["Anchor / Brand"] != "")]

    if valid_rows.empty:
        st.warning("Please paste or type some data into the table first!")
    else:
        results = []
        progress_text = "Launching Playwright automation browser..."
        my_bar = st.progress(0, text=progress_text)
        total_rows = len(valid_rows)

        # Initialize the Playwright session wrapper context manager
        with sync_playwright() as p:
            # Launch Chromium in headless mode
            browser = p.chromium.launch(headless=True)
            
            for index, row in valid_rows.iterrows():
                # Process individual link validation checks
                status, link_detected = verify_link_with_browser(
                    browser,
                    row["Published URL"], 
                    row["Anchor / Brand"], 
                    row["Target Page"],
                    timeout
                )
                
                results.append({
                    "Published URL": row["Published URL"],
                    "Anchor / Brand": row["Anchor / Brand"],
                    "Target Page": row["Target Page"] if pd.notna(row["Target Page"]) else "",
                    "Verification Status": status,
                    "Link Present?": link_detected
                })
                
                # Update UI elements
                current_progress = len(results) / total_rows
                my_bar.progress(current_progress, text=f"Checking {len(results)} of {total_rows} pages using Chromium...")
            
            # Close main global browser executable stream when completed
            browser.close()
            
        my_bar.empty()
        
        # Present results
        results_df = pd.DataFrame(results)
        st.subheader("📊 Verification Results")
        
        def style_status(val):
            if "✅" in str(val):
                return 'background-color: #d4edda; color: #155724;'
            elif "❌" in str(val):
                return 'background-color: #f8d7da; color: #721c24;'
            elif "⚠️" in str(val):
                return 'background-color: #fff3cd; color: #856404;'
            return ''

        try:
            styled_results = results_df.style.map(style_status, subset=['Verification Status'])
        except AttributeError:
            styled_results = results_df.style.applymap(style_status, subset=['Verification Status'])
            
        st.dataframe(styled_results, use_container_width=True)
        
        # Download pipeline Setup
        csv = results_df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Download Results as CSV",
            data=csv,
            file_name="playwright_verification_report.csv",
            mime="text/csv",
        )