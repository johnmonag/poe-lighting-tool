import streamlit as st
import google.generativeai as genai
import pandas as pd
import fitz  # PyMuPDF
from PIL import Image
import io
import json

# --- 1. SYSTEM CONFIGURATION & PRICING ---
PRICES = {
    "Cisco Catalyst 9300 (48-port PoE++)": 5500.00,
    "Molex CoreSync LED Fixture": 250.00,
    "Molex CoreSync Occupancy Sensor": 120.00,
    "Molex CoreSync Air Quality Sensor": 180.00,
    "Cat6a Cable (per node avg)": 45.00
}

LABOR_HOURS = {"fixture": 1.5, "sensor": 0.75, "switch": 4.0}

st.set_page_config(page_title="PoE Design Pro", layout="wide")

# --- 2. LOGIC FUNCTIONS ---

def pdf_to_images(pdf_file):
    """Converts PDF pages to images."""
    images = []
    pdf_document = fitz.open(stream=pdf_file.read(), filetype="pdf")
    for page_num in range(len(pdf_document)):
        page = pdf_document.load_page(page_num)
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        images.append(img)
    return images

def analyze_floorplan(image, api_key):
    """Sends image to Gemini using models that have a high probability of free quota."""
    genai.configure(api_key=api_key)
    
    # We are using 'gemini-flash-latest' because it's the stable version 
    # of the 1.5 Flash model which has the most reliable free tier.
    models_to_try = [
        'gemini-flash-latest',   # Your Item #16 (Most reliable free tier)
        'gemini-pro-latest',    # Your Item #18
        'gemini-1.5-flash'      # Standard fallback
    ]
    
    success_data = None
    diag_info = []

    for m_name in models_to_try:
        try:
            model = genai.GenerativeModel(m_name)
            
            prompt = """
            Analyze this floor plan image. 
            1. Identify every room and open work area.
            2. Estimate the area in square meters (sqm) for each.
            3. Return the result ONLY as a valid JSON array of objects.
            Format: [{"room_name": "Conference Room", "area_sqm": 35}]
            """
            
            response = model.generate_content([prompt, image])
            
            res_text = response.text
            if "```json" in res_text:
                res_text = res_text.split("```json")[1].split("```")[0]
            elif "```" in res_text:
                res_text = res_text.split("```")[1].split("```")[0]
            
            success_data = json.loads(res_text.strip())
            if success_data:
                return success_data
                
        except Exception as e:
            # If we get a 429 (Quota) or 404 (Not Found), we try the next model
            diag_info.append(f"{m_name} failed: {str(e)}")
            continue

    st.error("Quota or Access Error: The AI models are currently restricted on your Free Tier.")
    with st.expander("Why am I seeing this?"):
        st.write("Google has set a '0 limit' on newer models for some regions.")
        st.write("**How to fix:**")
        st.write("1. Wait 60 seconds and try again (standard rate limiting).")
        st.write("2. Go to [Google AI Studio](https://aistudio.google.com/) and check if you can use Gemini 1.5 Flash in the chat there.")
        st.write("3. Detailed log for developer:")
        st.write(diag_info)
    return None

def generate_calculations(rooms, labor_rate, include_aq):
    """Calculates Hardware and Labor."""
    results = []
    total_fixtures = total_occ = total_aq = 0

    for room in rooms:
        fixtures = max(1, round(room.get('area_sqm', 10) / 10))
        occ = 1
        aq = 1 if include_aq and room.get('area_sqm', 0) > 20 else 0
        total_fixtures += fixtures
        total_occ += occ
        total_aq += aq
        results.append({
            "Location": room.get('room_name', 'Unknown'),
            "Area (sqm)": room.get('area_sqm', 0),
            "Fixtures": fixtures, "Occ Sensors": occ, "AQ Sensors": aq
        })

    total_devices = total_fixtures + total_occ + total_aq
    switches = max(1, -(-total_devices // 38)) 
    
    boq = [
        {"Item": "Cisco Catalyst 9300 48P", "Qty": switches, "Cost": PRICES["Cisco Catalyst 9300 (48-port PoE++)"]},
        {"Item": "Molex LED Fixture", "Qty": total_fixtures, "Cost": PRICES["Molex CoreSync LED Fixture"]},
        {"Item": "Molex Occ Sensor", "Qty": total_occ, "Cost": PRICES["Molex CoreSync Occupancy Sensor"]},
        {"Item": "Molex AQ Sensor", "Qty": total_aq, "Cost": PRICES["Molex CoreSync Air Quality Sensor"]},
        {"Item": "Cat6a Cabling", "Qty": total_devices, "Cost": PRICES["Cat6a Cable (per node avg)"]}
    ]
    
    labor_hrs = (total_fixtures * 1.5) + ((total_occ + total_aq) * 0.75) + (switches * 4.0)
    boq.append({"Item": f"Labor (${labor_rate}/hr)", "Qty": labor_hrs, "Cost": labor_rate})
    
    return pd.DataFrame(results), pd.DataFrame(boq)

# --- 3. USER INTERFACE ---
st.title("💡 PoE Lighting & Building Estimator")

with st.sidebar:
    st.header("Settings")
    api_key = st.text_input("Gemini API Key", type="password")
    labor_toggle = st.checkbox("Apply $10/hr Labor Rate")
    aq_toggle = st.toggle("Include Air Quality Sensors", value=True)
    labor_rate = 10 if labor_toggle else 65

uploaded_file = st.file_uploader("Upload Floor Plan (PDF)", type="pdf")

if uploaded_file and api_key:
    if st.button("Generate Estimate"):
        with st.spinner("Analyzing PDF..."):
            images = pdf_to_images(uploaded_file)
            all_floors_details = {}
            summary_list = []

            for i, img in enumerate(images):
                rooms = analyze_floorplan(img, api_key)
                if rooms:
                    details_df, boq_df = generate_calculations(rooms, labor_rate, aq_toggle)
                    all_floors_details[f"Floor {i+1}"] = details_df
                    summary_list.append(boq_df)
            
            if summary_list:
                full_summary = pd.concat(summary_list).groupby("Item").agg({"Qty": "sum", "Cost": "first"}).reset_index()
                full_summary["Total"] = full_summary["Qty"] * full_summary["Cost"]
                st.success("Analysis Complete")
                st.subheader("Project Summary")
                st.table(full_summary)
                
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                    full_summary.to_excel(writer, sheet_name='Project Summary', index=False)
                    for f_name, df in all_floors_details.items():
                        df.to_excel(writer, sheet_name=f_name, index=False)
                
                st.download_button("📥 Download Excel", output.getvalue(), "Estimate.xlsx")
elif not api_key and uploaded_file:
    st.warning("Enter API Key to begin.")
