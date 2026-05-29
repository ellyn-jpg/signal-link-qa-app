import os
import subprocess
import sys
import streamlit as st

# Dynamically download the headless chromium browser files cleanly
@st.cache_resource
def install_playwright_browsers():
    try:
        if os.environ.get("STREAMLIT_RUNTIME_ENV") or not os.path.exists("/Users"):
            subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
    except Exception as e:
        st.error(f"Playwright Browser Sync Failed: {e}")

install_playwright_browsers()

# --- Core Application Dependencies ---
import pandas as pd
from playwright.sync_api import sync_playwright
from urllib.parse import urlparse, urljoin
import time

# Set up page configuration
st.set_page_config(page_title="Link Building Verifier", page_icon="🔗", layout="wide")

st.title("🔗 Link Building Status Verifier")
st.markdown("""
Paste your data directly from Excel or Google Sheets into the table below. 
* **If a Target Page is provided:** The tool verifies the anchor text exists AND points to that specific URL.
* **If the Target Page is BLANK:** The tool simply verifies that the Anchor/Brand exists on the page.
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
    1. **Copy** 3 columns from your worksheet.
    2. **Click** the first cell of the table on the right.
    3. **Paste (Ctrl+V / Cmd+V)** your data.
    4. Click **Run Verification Check**.
    """)
    st.markdown("---")
    timeout = st.slider("Page Wait Timeout (seconds)", min_value=5, max_value=30, value=15, 
                        help="Time given to modern JavaScript heavy sites to fully render content.")

# Data Editor interface allowing easy copy-paste
st.subheader("📋 Input Worksheet Data")
edited_df = st.data_editor(
    DEFAULT_DF,
    num_rows="dynamic",
    use_container_width=True,
    column_config={
        "Published URL": st.column_config.TextColumn("Published URL", help="e.g., https://reddit.com/r/..."),
        "Anchor / Brand": st.column_config.TextColumn("Anchor Term / Brand Name", help="e.g., BrandName"),
        "Target Page": st.column_config.TextColumn("Target Page (Optional)", help="Leave blank if you only care about a mention"),
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
    """Uses a real headless browser instance engineered to bypass 403 walls and parse Shadow DOMs."""
    pub_url = str(pub_url).strip()
    anchor = str(anchor).strip().lower()
    
    has_target = pd.notna(target_url) and str(target_url).strip() != ""
    target_url = str(target_url).strip() if has_target else ""

    if not pub_url or not anchor:
        return "⚠️ Missing Input Data", "No", "No"

    context = None
    page = None
    try:
        # Fortify context headers to look exactly like a real user desktop browser profile
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1440, "height": 900},
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1"
            }
        )
        page = context.new_page()

        # Stop cloud container execution from downloading unnecessary layout assets
        def block_aggressively(route):
            if route.request.resource_type in ["image", "media", "font"]:
                route.abort()
            else:
                route.continue_()
        
        page.route("**/*", block_aggressively)
        
        # Navigate and give the server ample time to negotiate anti-bot hands-shakes
        response = page.goto(pub_url, timeout=timeout_secs * 1000, wait_until="networkidle")
        
        if not response or response.status != 200:
            status_code = response.status if response else "Unknown"
            return f"❌ Broken (HTTP {status_code})", "No", "No"

        # Give Reddit/Quora script frameworks a moment to layout comments
        time.sleep(3.0)

        # 1. Safe Shadow DOM Extraction (Handles null objects gracefully)
        page_text_with_shadows = page.evaluate("""() => {
            function getDeepInnerText(node) {
                let text = "";
                if (!node) return text;
                if (node.nodeType === Node.TEXT_NODE) {
                    text += node.nodeValue || "";
                }
                if (node.childNodes) {
                    for (let child of node.childNodes) {
                        text += getDeepInnerText(child);
                    }
                }
                if (node.shadowRoot) {
                    for (let child of node.shadowRoot.childNodes) {
                        text += getDeepInnerText(child);
                    }
                }
                return text;
            }
            const rawText = getDeepInnerText(document.body);
            return rawText ? rawText.toLowerCase() : "";
        }""")

        if not page_text_with_shadows or anchor not in page_text_with_shadows:
            return "❌ Anchor text not found on page", "No", "No"
        
        brand_present = "Yes"

        # 2. Extract Hyperlinks cleanly across open Shadow roots
        links = page.evaluate("""() => {
            function getAllLinks(node, foundLinks = []) {
                if (!node) return foundLinks;
                if (node.tagName === 'A' && node.href) {
                    foundLinks.push({
                        href: node.href,
                        text: node.innerText || node.textContent || ''
                    });
                }
                if (node.childNodes) {
                    for (let child of node.childNodes) {
                        getAllLinks(child, foundLinks);
                    }
                }
                if (node.shadowRoot) {
                    for (let child of node.shadowRoot.childNodes) {
                        getAllLinks(child, foundLinks);
                    }
                }
                return foundLinks;
            }
            return getAllLinks(document.body);
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

            if anchor in link_text:
                anchor_is_linked_somewhere = True
                linked_to_url = absolute_href

            if has_target:
                if anchor in link_text and normalized_href == normalized_target:
                    return "✅ Link Verified & Live", brand_present, "Yes"
                if normalized_href == normalized_target:
                    link_found_with_wrong_anchor = True
                    actual_anchor = link['text'].strip()

        # 3. Final outcome presentation logic routing
        if has_target:
            if link_found_with_wrong_anchor:
                return f"⚠️ Target linked, but used wrong anchor: '{actual_anchor}'", brand_present, "Partial"
            if anchor_is_linked_somewhere:
                return f"⚠️ Anchor found, but links to wrong URL: {linked_to_url}", brand_present, "Partial"
            return "❌ Anchor exists as plain text, but is NOT hyperlinked", brand_present, "No"
        else:
            if anchor_is_linked_somewhere:
                return f"✅ Mention Verified (Linked to: {linked_to_url})", brand_present, "Yes"
            return "✅ Mention Verified (Plain Text / Unlinked)", brand_present, "No"

    except Exception as e:
        error_msg = str(e).replace('"', "'")
        return f"❌ Browser Error/Timeout: {error_msg[:45]}...", "No", "No"
    finally:
        if page: page.close()
        if context: context.close()


# Action execution button
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

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            
            for index, row in valid_rows.iterrows():
                status, brand_present, link_detected = verify_link_with_browser(
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
                    "Link Status": status,
                    "Anchor/Brand Present?": brand_present,
                    "Link Present?": link_detected
                })
                
                current_progress = len(results) / total_rows
                my_bar.progress(current_progress, text=f"Checking {len(results)} of {total_rows} pages using Chromium...")
            
            browser.close()
            
        my_bar.empty()
        
        # Display Results Table
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
            styled_results = results_df.style.map(style_status, subset=['Link Status'])
        except AttributeError:
            styled_results = results_df.style.applymap(style_status, subset=['Link Status'])
            
        st.dataframe(
            styled_results, 
            use_container_width=True,
            column_config={
                "Published URL": st.column_config.TextColumn("Published URL", width=150),
                "Anchor / Brand": st.column_config.TextColumn("Anchor / Brand"),
                "Target Page": st.column_config.TextColumn("Target Page"),
                "Link Status": st.column_config.TextColumn("Link Status"),
                "Anchor/Brand Present?": st.column_config.TextColumn("Anchor/Brand Present?"),
                "Link Present?": st.column_config.TextColumn("Link Present?"),
            }
        )
        
        # Export download pipeline
        csv = results_df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Download Results as CSV",
            data=csv,
            file_name="playwright_verification_report.csv",
            mime="text/csv",
        )