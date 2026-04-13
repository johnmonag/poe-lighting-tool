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

LABOR_HOURS = {
    "fixture": 1.5,
    "sensor": 0.75,
    "switch": 4.0
}

st.set_page_config(page_title="PoE Design Pro", layout="wide")

# --- 2. LOGIC FUNCTIONS ---

def pdf_to_images(pdf_file):
    """Converts PDF pages to images for Gemini processing."""
    images = []
    pdf_document = fitz.open(stream=pdf_file.read(), filetype="pdf")
    for page_num in range(len(pdf_document)):
        page = pdf_document.load_page(page_num)
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        images.append(img)
    return images

def analyze_floorplan(image, api_key):
    """Sends image to Gemini to extract room data."""
    genai.configure(api_key=api_key)
    
    # Updated to 'gemini-1.5-flash' for better compatibility
    model = genai.GenerativeModel('gemini-1.5-flash')
    
    prompt = """
    Analyze this floor plan. Identify all rooms and open work areas.
    Estimate the area in square meters (sqm) for each.
    Return the data as a JSON array of objects.
    Example format: [{"room_name": "Office 101", "area_sqm": 20}]
    """
    try:
        # We add 'stream=False' for more stable web responses
        response = model.generate_content([prompt, image])
        
        # This part helps clean up the text if Gemini adds extra formatting
        text_response = response.text
        if "```json" in text_response:
            text_response = text_response.split("```json")[1].split("```")[0]
        elif "```" in text_response:
            text_response = text_response.split("```")[1].split("```")[0]
            
        return json.loads(text_response.strip())
    except Exception as e:
        st.error(f"AI Error: {e}")
        return None

def generate_calculations(rooms, labor_rate, include_aq):
    """Calculates Hardware and Labor based on detected rooms."""
    results = []
    total_fixtures = 0
    total_occ = 0
    total_aq = 0

    for room in rooms:
        # Rule: 1 fixture per 10sqm
        fixtures = max(1, round(room['area_sqm'] / 10))
        occ = 1
        aq = 1 if include_aq and room['area_sqm'] > 20 else 0
        
        total_fixtures += fixtures
        total_occ += occ
        total_aq += aq
        
        results.append({
            "Location": room['room_name'],
            "Area (sqm)": room['area_sqm'],
            "Fixtures": fixtures,
            "Occ Sensors": occ,
            "AQ Sensors": aq
        })

    # Switch logic: 48 ports, 80% capacity = 38 devices per switch
    total_devices = total_fixtures + total_occ + total_aq
    switches = max(1, -(-total_devices // 38)) 
    
    # Costs
    boq = [
        {"Item": "Cisco Catalyst 9300 48P", "Qty": switches, "Cost": PRICES["Cisco Catalyst 9300 (48-port PoE++)"]},
        {"Item": "Molex LED Fixture", "Qty": total_fixtures, "Cost": PRICES["Molex CoreSync LED Fixture"]},
        {"Item": "Molex Occ Sensor", "Qty": total_occ, "Cost": PRICES["Molex CoreSync Occupancy Sensor"]},
        {"Item": "Molex AQ Sensor", "Qty": total_aq, "Cost": PRICES["Molex CoreSync Air Quality Sensor"]},
        {"Item": "Cat6a Cabling", "Qty": total_devices, "Cost": PRICES["Cat6a Cable (per node avg)"]}
    ]
    
    labor_total_hrs = (total_fixtures * LABOR_HOURS["fixture"]) + \
                      ((total_occ + total_aq) * LABOR_HOURS["sensor"]) + \
                      (switches * LABOR_HOURS["switch"])
    
    boq.append({"Item": f"Labor (${labor_rate}/hr)", "Qty": labor_total_hrs, "Cost": labor_rate})
    
    return pd.DataFrame(results), pd.DataFrame(boq)

# --- 3. USER INTERFACE ---

st.title("💡 PoE Lighting & Building Estimator")
st.write("Upload a floor plan. Gemini will calculate areas and generate a Cisco/Molex Bill of Quantities.")

with st.sidebar:
    st.header("Settings")
    api_key = st.text_input("Gemini API Key", type="password")
    labor_toggle = st.checkbox("Apply $10/hr Labor Rate")
    aq_toggle = st.toggle("Include Air Quality Sensors", value=True)
    labor_rate = 10 if labor_toggle else 65
    st.info(f"Active Labor Rate: ${labor_rate}/hr")

uploaded_file = st.file_uploader("Upload Floor Plan (PDF)", type="pdf")

if uploaded_file and api_key:
    if st.button("Generate Estimate"):
        with st.spinner("Analyzing PDF with AI..."):
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
                # Merge summaries
                full_summary = pd.concat(summary_list).groupby("Item").agg({"Qty": "sum", "Cost": "first"}).reset_index()
                full_summary["Total"] = full_summary["Qty"] * full_summary["Cost"]
                
                st.success("Analysis Complete")
                
                # Show Preview
                st.subheader("Project Summary")
                st.table(full_summary)
                st.write(f"### Total Project Est: ${full_summary['Total'].sum():,.2f}")

                # Excel Export
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                    full_summary.to_excel(writer, sheet_name='Project Summary', index=False)
                    for floor_name, df in all_floors_details.items():
                        df.to_excel(writer, sheet_name=floor_name, index=False)
                
                st.download_button(
                    label="📥 Download Excel Spreadsheet",
                    data=output.getvalue(),
                    file_name="PoE_Estimate.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
elif not api_key and uploaded_file:
    st.warning("Please enter your Gemini API Key in the sidebar.")
