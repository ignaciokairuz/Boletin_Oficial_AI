"""
Boletín Oficial - Análisis v13 TURBO
------------------------------------
PERFORMANCE: Parallel processing with ThreadPoolExecutor
- PDF Downloads: 10 concurrent threads
- AI Summaries: 5 concurrent threads
Expected time: 30-60 min (down from 3+ hours)
"""
import requests
import json
import io
import re
import time
import os
from datetime import datetime
from pypdf import PdfReader
from gradio_client import Client
from concurrent.futures import ThreadPoolExecutor, as_completed

# Selenium imports
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

API_URL = "https://api-restboletinoficial.buenosaires.gob.ar/obtenerBoletin/0/true"
BAC_URL = "https://www.buenosairescompras.gob.ar/ListarAperturaUltimos30Dias.aspx"
AMOUNT_REGEX = r'\$\s?(\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?)'
DATA_DIR = "datos"

# Parallelism settings
PDF_WORKERS = 10
AI_WORKERS = 5

def clean_organismo(org):
    if not org: return "Otros"
    return org.strip().rstrip('-').strip()

def extract_amounts(text):
    if not text: return []
    amounts = []
    for m in re.finditer(AMOUNT_REGEX, text):
        val_str = m.group(1).replace('.', '').replace(',', '.')
        try:
            val = float(val_str)
            if val > 0: amounts.append(val)
        except: continue
    return amounts

def clean_ai_response(text):
    if not text: return ""
    if "Error code:" in text or "BadRequestError" in text: return "Ver documento"
    result = text
    markers = ["assistantfinal", "final**", "**💬", "Response:**", "**Título:**", 
               "🤔 Analysis:", "Analysis:**", "Análisis:**"]
    for m in markers:
        if m in result: result = result.split(m)[-1]
    result = re.sub(r'\*\*([^*]+)\*\*', r'\1', result)
    result = re.sub(r'\*([^*]+)\*', r'\1', result)
    result = re.sub(r'`([^`]+)`', r'\1', result)
    result = re.sub(r'#{1,6}\s+', '', result)
    return result.strip()[:600]

def process_norm_parallel(item):
    """Process a single norm - designed for parallel execution"""
    try:
        r = requests.get(item['url'], timeout=60)
        if r.status_code != 200: return None
        with io.BytesIO(r.content) as f:
            reader = PdfReader(f)
            text = "".join([p.extract_text() + "\n" for p in reader.pages])
        amounts = extract_amounts(text)
        item['text_snippet'] = text[:600]
        if amounts:
            item['monto'] = max(amounts)
            item['monto_fmt'] = f"${item['monto']:,.2f}"
            item['tiene_gasto'] = True
        else:
            item['tiene_gasto'] = False
        return item
    except:
        return None

def process_anexo_parallel(anexo):
    """Extract text from anexo - for parallel execution"""
    try:
        r = requests.get(anexo['url'], timeout=30)
        if r.status_code != 200: return None
        with io.BytesIO(r.content) as f:
            reader = PdfReader(f)
            text = reader.pages[0].extract_text() if reader.pages else ""
        return text[:300]
    except: 
        return None

def get_ai_summary_safe(client, prompt, system_prompt, max_chars=300):
    """Thread-safe AI summary with aggressive truncation"""
    try:
        result = client.predict(
            message=prompt[:max_chars], 
            system_prompt=system_prompt, 
            temperature=0.3, 
            api_name="/chat"
        )
        return clean_ai_response(result)
    except:
        return None

def extract_monto_from_detail(driver):
    """Extract amount from licitación detail page."""
    try:
        # Look for the "Monto" text in the page
        page_text = driver.page_source
        # Common patterns for amounts in BAC
        monto_patterns = [
            r'Monto.*?\$\s*([\d.,]+)',
            r'Monto del contrato.*?\$\s*([\d.,]+)',
            r'\$\s*([\d]{1,3}(?:\.\d{3})*(?:,\d{2})?)'
        ]
        for pattern in monto_patterns:
            match = re.search(pattern, page_text, re.IGNORECASE | re.DOTALL)
            if match:
                val_str = match.group(1).replace('.', '').replace(',', '.')
                try:
                    val = float(val_str)
                    if val > 1000:  # Filter out small numbers that aren't amounts
                        return val
                except:
                    continue
        return None
    except:
        return None

def scrape_licitaciones(fecha_hoy):
    """Scrape Buenos Aires Compras - Multi-page with amount extraction"""
    print(f"\n🏛️ Scrapeando licitaciones de {fecha_hoy}...")
    licitaciones = []
    driver = None
    
    try:
        options = Options()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        driver.get("https://www.buenosairescompras.gob.ar/")
        time.sleep(2)
        driver.get(BAC_URL)
        time.sleep(2)
        
        today_parts = fecha_hoy.split('/')
        today_str = f"{today_parts[0]}/{today_parts[1]}/{today_parts[2]}"
        
        # First pass: collect basic info from list
        temp_lics = []
        page_num = 1
        while page_num <= 5:  # Max 5 pages for list
            print(f"   📄 Página {page_num}...")
            try:
                WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.ID, "ctl00_CPH1_GridListaPliegos")))
                table = driver.find_element(By.ID, "ctl00_CPH1_GridListaPliegos")
                rows = table.find_elements(By.TAG_NAME, "tr")[1:]
                
                for row in rows:
                    try:
                        cols = row.find_elements(By.TAG_NAME, "td")
                        if len(cols) < 6: continue
                        if today_str in cols[3].text.strip():
                            # Get the link to detail page
                            link_elem = cols[0].find_element(By.TAG_NAME, "a")
                            detail_url = link_elem.get_attribute("href")
                            numero = cols[0].text.strip()
                            temp_lics.append({
                                'numero': numero,
                                'nombre': cols[1].text.strip(),
                                'tipo': cols[2].text.strip(),
                                'fecha': cols[3].text.strip().split()[0],
                                'estado': cols[4].text.strip(),
                                'unidad': cols[5].text.strip(),
                                'detail_url': detail_url,
                                'url': f"https://www.buenosairescompras.gob.ar/GCBA/buscadorDePliegos.aspx?id={numero}"
                            })
                    except: continue
                
                try:
                    next_link = driver.find_element(By.XPATH, f"//a[contains(@href,'Page${page_num + 1}')]")
                    driver.execute_script("arguments[0].click();", next_link)
                    time.sleep(2)
                    page_num += 1
                except:
                    break
            except:
                break
        
        print(f"   📋 Encontradas {len(temp_lics)} licitaciones. Extrayendo montos...")
        
        # Second pass: visit each detail page to get amount
        for i, lic in enumerate(temp_lics):
            try:
                if lic.get('detail_url'):
                    driver.get(lic['detail_url'])
                    time.sleep(1.5)
                    monto = extract_monto_from_detail(driver)
                    if monto:
                        lic['monto'] = monto
                        lic['monto_fmt'] = f"${monto:,.2f}"
                        print(f"     [{i+1}/{len(temp_lics)}] {lic['numero']}: ${monto:,.0f}")
                    else:
                        lic['monto_fmt'] = "Monto no especificado"
                        print(f"     [{i+1}/{len(temp_lics)}] {lic['numero']}: Sin monto")
            except Exception as e:
                lic['monto_fmt'] = "Error extrayendo monto"
            
            lic['resumen_ia'] = f"{lic['tipo']} - {lic['nombre']}"
            del lic['detail_url']  # Clean up
            licitaciones.append(lic)
        
        print(f"   ✅ Total: {len(licitaciones)}")
        driver.quit()
        return licitaciones, True
    except Exception as e:
        print(f"⚠️ Scraping error: {e}")
        if driver: driver.quit()
        return [], False


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    start_time = time.time()
    
    print("📥 Consultando API Boletín...")
    try:
        response = requests.get(API_URL, timeout=30)
        data = response.json()
    except: return

    boletin = data.get('boletin', {})
    fecha_raw = boletin.get('fecha_publicacion', '?')
    
    try:
        fp = fecha_raw.split('/')
        fecha_iso = f"{fp[2]}-{fp[1].zfill(2)}-{fp[0].zfill(2)}"
    except:
        fecha_iso = datetime.now().strftime('%Y-%m-%d')
    
    data_file = os.path.join(DATA_DIR, f"{fecha_iso}.json")
    pending_file = os.path.join(DATA_DIR, f"{fecha_iso}_pendientes.json")
    
    print(f"📋 Boletín ({fecha_iso})")
    
    existing_data = None
    pending_state = {}
    
    if os.path.exists(data_file):
        with open(data_file, 'r', encoding='utf-8') as f: existing_data = json.load(f)
    if os.path.exists(pending_file):
        with open(pending_file, 'r', encoding='utf-8') as f: pending_state = json.load(f)
    
    if existing_data and len(existing_data.get('licitaciones', [])) == 0:
        pending_state['licitaciones_necesarias'] = True
            
    if existing_data and not pending_state:
        print("✅ Día completo. Regenerando HTML.")
        regenerate_html()
        return

    if not existing_data:
        print("🆕 MODO INICIAL - Extrayendo normas...")
        existing_data = {
            'fecha': fecha_iso, 'fecha_display': fecha_raw,
            'gastos': [], 'sin_gastos': [], 'licitaciones': [], 'organismos': []
        }
        normas_pendientes = []
        normas_root = data.get('normas', {}).get('normas', {})
        for poder, tipos in normas_root.items():
            for tipo, organismos in tipos.items():
                for organismo, lista in organismos.items():
                    org_clean = clean_organismo(organismo)
                    for item in lista:
                        anexos = [{'nombre': a.get('nombre_anexo', ''), 'url': a.get('filenet_firmado', '')} for a in item.get('anexos', [])]
                        normas_pendientes.append({
                            'nombre': item.get('nombre'), 'sumario': item.get('sumario'), 'url': item.get('url_norma'),
                            'tipo': tipo, 'organismo': org_clean, 'anexos': anexos
                        })
        
        pending_state = {
            'normas_pendientes': normas_pendientes,
            'licitaciones_necesarias': True,
            'resumenes_pendientes': True
        }

    # ============ PARALLEL PDF PROCESSING ============
    if pending_state.get('normas_pendientes'):
        pend = pending_state['normas_pendientes']
        print(f"🚀 Procesando {len(pend)} normas en PARALELO ({PDF_WORKERS} workers)...")
        
        resultados = []
        with ThreadPoolExecutor(max_workers=PDF_WORKERS) as executor:
            futures = {executor.submit(process_norm_parallel, item): item for item in pend}
            for i, future in enumerate(as_completed(futures)):
                if (i+1) % 20 == 0:
                    print(f"   Progreso: {i+1}/{len(pend)}")
                result = future.result()
                if result:
                    resultados.append(result)
        
        for r in resultados:
            if r['tiene_gasto']:
                existing_data['gastos'].append(r)
            else:
                existing_data['sin_gastos'].append(r)
        
        pending_state['normas_pendientes'] = []
        with open(data_file, 'w', encoding='utf-8') as f: 
            json.dump(existing_data, f, indent=2, ensure_ascii=False)
        print(f"   ✅ {len(resultados)} normas procesadas")

    # ============ LICITACIONES ============
    if pending_state.get('licitaciones_necesarias'):
        lics, success = scrape_licitaciones(fecha_raw)
        if success and len(lics) > 0:
            existing_data['licitaciones'] = lics
            pending_state['licitaciones_necesarias'] = False
            with open(data_file, 'w', encoding='utf-8') as f: 
                json.dump(existing_data, f, indent=2, ensure_ascii=False)

    # ============ PARALLEL AI SUMMARIES ============
    if pending_state.get('resumenes_pendientes'):
        print(f"\n🤖 Generando resúmenes IA en PARALELO ({AI_WORKERS} workers)...")
        
        try:
            client = Client("amd/gpt-oss-120b-chatbot")
            CORTO = "Responde SOLO con un título de 5-8 palabras."
            LARGO = "Explica en 4 oraciones sencillas qué se compra/hace y para qué."
            
            # Prepare all items needing summaries
            items_to_process = []
            for g in existing_data['gastos']:
                if not g.get('resumen_corto'):
                    items_to_process.append(('gasto', g))
            for s in existing_data['sin_gastos'][:50]:
                if not s.get('resumen_corto'):
                    items_to_process.append(('norma', s))
            
            def process_ai_item(item_tuple):
                tipo, item = item_tuple
                prompt = f"{item['nombre']}\n{item.get('text_snippet', item.get('sumario', ''))[:250]}"
                corto = get_ai_summary_safe(client, prompt, CORTO, 250)
                largo = get_ai_summary_safe(client, prompt, LARGO, 250)
                return tipo, item, corto, largo
            
            print(f"   Procesando {len(items_to_process)} items...")
            with ThreadPoolExecutor(max_workers=AI_WORKERS) as executor:
                futures = [executor.submit(process_ai_item, it) for it in items_to_process]
                done = 0
                for future in as_completed(futures):
                    done += 1
                    if done % 10 == 0:
                        print(f"   AI Progreso: {done}/{len(items_to_process)}")
                    try:
                        tipo, item, corto, largo = future.result()
                        if corto:
                            item['resumen_corto'] = corto
                            item['resumen_largo'] = largo or item.get('sumario', '')[:300]
                        else:
                            item['resumen_corto'] = item.get('sumario', '')[:80] or "Sin resumen"
                            item['resumen_largo'] = item.get('sumario', '')[:300]
                    except: pass
            
            # Anexos (parallel but limited)
            print("   📎 Procesando anexos...")
            all_anexos = []
            for n in existing_data['gastos'] + existing_data['sin_gastos']:
                for a in n.get('anexos', []):
                    if not a.get('resumen') or "Anexo de:" in a.get('resumen', ''):
                        all_anexos.append((n, a))
            
            def process_anexo_ai(item_tuple):
                norm, anexo = item_tuple
                txt = process_anexo_parallel(anexo)
                if txt:
                    res = get_ai_summary_safe(client, f"Resume: {txt}", "1 oración simple", 200)
                    return anexo, res if res else f"Anexo de: {norm.get('nombre')[:50]}"
                return anexo, f"Anexo de: {norm.get('nombre')[:50]}"
            
            with ThreadPoolExecutor(max_workers=AI_WORKERS) as executor:
                futures = [executor.submit(process_anexo_ai, it) for it in all_anexos[:100]]
                for future in as_completed(futures):
                    try:
                        anexo, resumen = future.result()
                        anexo['resumen'] = resumen
                    except: pass
            
            pending_state['resumenes_pendientes'] = False
            print("   ✅ Resúmenes completados")
                
        except Exception as e:
            print(f"❌ Error IA: {e}")

    # CLEANUP
    existing_data['organismos'] = sorted(list(set(g['organismo'] for g in existing_data['gastos'])))
    existing_data['gastos'].sort(key=lambda x: x.get('monto', 0), reverse=True)
    
    with open(data_file, 'w', encoding='utf-8') as f: 
        json.dump(existing_data, f, indent=2, ensure_ascii=False)
        
    if not any(pending_state.values()):
        if os.path.exists(pending_file): os.remove(pending_file)
    else:
        with open(pending_file, 'w', encoding='utf-8') as f: 
            json.dump(pending_state, f, indent=2, ensure_ascii=False)

    elapsed = time.time() - start_time
    print(f"\n⏱️ Tiempo total: {elapsed/60:.1f} minutos")
    
    regenerate_html()

def regenerate_html():
    print("🌍 Generando HTML...")
    if not os.path.exists(DATA_DIR): return
    dates = sorted([f.replace('.json','') for f in os.listdir(DATA_DIR) if f.endswith('.json') and '_pendientes' not in f], reverse=True)
    if not dates: return
    
    all_data = {}
    for d in dates:
        try:
            with open(os.path.join(DATA_DIR, f"{d}.json"), 'r', encoding='utf-8') as f: 
                all_data[d] = json.load(f)
        except: pass
        
    html = f'''<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Monitor de Gastos Públicos</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {{ --bg-primary: #1a1a2e; --bg-secondary: #16213e; --bg-tertiary: #0f3460; --text-primary: #eee; --text-secondary: #aaa; --accent: #e94560; --success: #4ecca3; }}
        body.light-mode {{ --bg-primary: #f5f5f5; --bg-secondary: #fff; --bg-tertiary: #e0e0e0; --text-primary: #333; --text-secondary: #666; }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: system-ui, sans-serif; background: var(--bg-primary); color: var(--text-primary); }}
        .container {{ display: flex; min-height: 100vh; }}
        .sidebar {{ width: 260px; background: var(--bg-secondary); padding: 20px; border-right: 1px solid var(--bg-tertiary); position: fixed; height: 100vh; overflow-y: auto; }}
        .sidebar h2 {{ color: var(--accent); margin-bottom: 20px; }}
        .sidebar-section {{ margin-bottom: 20px; }}
        .sidebar-section h3 {{ color: var(--text-secondary); font-size: 0.75em; text-transform: uppercase; margin-bottom: 8px; }}
        .date-select, .filter-select {{ width: 100%; padding: 10px; border-radius: 6px; background: var(--accent); color: white; border: none; font-weight: bold; cursor: pointer; }}
        .filter-select {{ background: var(--bg-tertiary); margin-top: 10px; }}
        .tab-list {{ list-style: none; }}
        .tab-list li {{ padding: 8px 10px; cursor: pointer; border-radius: 5px; margin-bottom: 3px; font-size: 0.9em; }}
        .tab-list li:hover {{ background: var(--bg-tertiary); }}
        .tab-list li.active {{ background: var(--bg-tertiary); border-left: 3px solid var(--accent); }}
        .main {{ margin-left: 260px; flex: 1; padding: 25px; }}
        .header {{ background: linear-gradient(135deg, var(--bg-tertiary), var(--bg-secondary)); padding: 20px; border-radius: 10px; margin-bottom: 20px; }}
        .stats {{ display: flex; gap: 10px; margin-top: 10px; flex-wrap: wrap; }}
        .stat {{ background: rgba(233,69,96,0.15); padding: 8px 12px; border-radius: 6px; }}
        .stat-value {{ font-size: 1.2em; font-weight: bold; color: var(--accent); }}
        .stat-label {{ font-size: 0.7em; color: var(--text-secondary); }}
        .card-grid {{ display: grid; gap: 12px; }}
        .card {{ background: var(--bg-secondary); padding: 15px; border-radius: 8px; border-left: 3px solid var(--bg-tertiary); }}
        .card.expensive {{ border-left-color: var(--accent); }}
        .card .amount {{ font-size: 1.1em; font-weight: bold; color: var(--success); margin-bottom: 5px; }}
        .card.expensive .amount {{ color: var(--accent); }}
        .card .desc {{ color: var(--text-secondary); line-height: 1.4; margin-bottom: 8px; font-size: 0.9em; }}
        .card .desc-long {{ display: none; margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--bg-tertiary); font-size: 0.85em; }}
        .card.expanded .desc-long {{ display: block; }}
        .card .meta {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}
        .tag {{ background: var(--bg-tertiary); padding: 3px 8px; border-radius: 4px; font-size: 0.75em; }}
        .btn {{ background: var(--accent); color: white; padding: 5px 10px; border-radius: 4px; text-decoration: none; font-size: 0.8em; cursor: pointer; border: none; }}
        .btn.secondary {{ background: var(--bg-tertiary); color: var(--text-primary); }}
        .tab-content {{ display: none; }}
        .tab-content.active {{ display: block; }}
        .chart-container {{ background: var(--bg-secondary); padding: 15px; border-radius: 10px; height: 400px; }}
    </style>
</head>
<body>
    <div class="container">
        <aside class="sidebar">
            <h2>🔍 Monitor</h2>
            <div class="sidebar-section">
                <h3>📅 Fecha</h3>
                <select id="dateSelect" class="date-select" onchange="loadDate(this.value)"></select>
            </div>
            <div class="sidebar-section">
                <h3>🏛️ Organismo</h3>
                <select id="filterOrganismo" class="filter-select" onchange="filterCards()"></select>
            </div>
            <div class="sidebar-section">
                <h3>📋 Vista</h3>
                <ul class="tab-list">
                    <li class="active" onclick="showTab('gastos')">💰 Gastos</li>
                    <li onclick="showTab('licitaciones')">🏛️ Licitaciones</li>
                    <li onclick="showTab('otros')">📄 Otras Normas</li>
                    <li onclick="showTab('anexos')">📎 Anexos</li>
                    <li onclick="showTab('stats')">📊 Estadísticas</li>
                </ul>
            </div>
        </aside>
        <main class="main">
            <div class="header">
                <h1>Boletín N° <span id="numBoletin">-</span></h1>
                <p>Fecha: <span id="fechaDisplay">-</span></p>
                <div class="stats">
                    <div class="stat"><div class="stat-value" id="statGastos">-</div><div class="stat-label">Gastos</div></div>
                    <div class="stat"><div class="stat-value" id="statLic">-</div><div class="stat-label">Licitaciones</div></div>
                    <div class="stat"><div class="stat-value" id="statAnexos">-</div><div class="stat-label">Anexos</div></div>
                </div>
            </div>
            <div class="tab-content active" id="tab-gastos"><div class="card-grid" id="gastosGrid"></div></div>
            <div class="tab-content" id="tab-licitaciones"><div class="card-grid" id="licitacionesGrid"></div></div>
            <div class="tab-content" id="tab-otros"><div class="card-grid" id="otrosGrid"></div></div>
            <div class="tab-content" id="tab-anexos"><div class="card-grid" id="anexosGrid"></div></div>
            <div class="tab-content" id="tab-stats"><div class="chart-container"><canvas id="statsChart"></canvas></div></div>
        </main>
    </div>
    
    <script>
        const allData = {json.dumps(all_data, ensure_ascii=False)};
        const sortedDates = Object.keys(allData).sort().reverse();
        let currentChart = null;
        
        function init() {{
            const dateSelect = document.getElementById('dateSelect');
            sortedDates.forEach(date => {{
                const opt = document.createElement('option');
                opt.value = date;
                opt.textContent = allData[date].fecha_display || date;
                dateSelect.appendChild(opt);
            }});
            if(sortedDates.length > 0) loadDate(sortedDates[0]);
        }}

        function loadDate(date) {{
            const d = allData[date];
            if (!d) return;

            document.getElementById('dateSelect').value = date;
            document.getElementById('numBoletin').textContent = d.numero_boletin || '-';
            document.getElementById('fechaDisplay').textContent = d.fecha_display || date;
            document.getElementById('statGastos').textContent = (d.gastos || []).length;
            document.getElementById('statLic').textContent = (d.licitaciones || []).length;
            
            let totalAnexos = 0;
            [...(d.gastos||[]), ...(d.sin_gastos||[])].forEach(n => totalAnexos += (n.anexos || []).length);
            document.getElementById('statAnexos').textContent = totalAnexos;
            
            const filter = document.getElementById('filterOrganismo');
            filter.innerHTML = '<option value="">Todos</option>';
            const orgs = new Set();
            (d.gastos || []).forEach(g => orgs.add(g.organismo || 'Otros'));
            Array.from(orgs).sort().forEach(o => {{
                const opt = document.createElement('option');
                opt.value = o; opt.textContent = o.substring(0,30);
                filter.appendChild(opt);
            }});

            document.getElementById('gastosGrid').innerHTML = (d.gastos || []).map(g => {{
                const expensive = (g.monto || 0) > 100000000 ? 'expensive' : '';
                const anexosHtml = (g.anexos || []).length > 0 
                    ? `<div class="anexos-list" style="margin-top:8px;padding-top:8px;border-top:1px solid var(--bg-tertiary);">
                        <small style="color:var(--text-secondary);">📎 Anexos:</small>
                        ${{(g.anexos || []).map(a => `<a href="${{a.url}}" target="_blank" class="tag" style="margin-left:4px;text-decoration:none;">${{(a.nombre || 'Anexo').substring(0,20)}}</a>`).join('')}}
                       </div>` 
                    : '';
                const resumenLargo = g.resumen_largo || g.sumario || g.text_snippet || 'Sin información adicional disponible.';
                return `<div class="card ${{expensive}}" data-org="${{g.organismo}}">
                    <div class="amount">${{g.monto_fmt || '$0'}}</div>
                    <div class="desc"><strong>${{g.resumen_corto || g.nombre || 'Sin título'}}</strong></div>
                    <div class="desc-long">${{resumenLargo}}${{anexosHtml}}</div>
                    <div class="meta">
                        <span class="tag">${{(g.organismo || '').substring(0,25)}}</span>
                        <button class="btn secondary" onclick="this.closest('.card').classList.toggle('expanded')">Ver más</button>
                        <a href="${{g.url || '#'}}" target="_blank" class="btn">PDF</a>
                    </div>
                </div>`;
            }}).join('') || '<p>No hay gastos</p>';

            document.getElementById('licitacionesGrid').innerHTML = (d.licitaciones || []).map(l => {{
                const hasMonto = l.monto && l.monto > 0;
                const montoClass = hasMonto ? (l.monto > 10000000 ? 'expensive' : '') : '';
                const montoDisplay = l.monto_fmt || 'Monto no especificado';
                return `<div class="card ${{montoClass}}">
                    <div class="amount" style="${{hasMonto ? '' : 'color:var(--text-secondary);font-size:0.9em;'}}">${{montoDisplay}}</div>
                    <div class="desc"><strong>${{l.numero || ''}}</strong> - ${{l.nombre || ''}}</div>
                    <div class="desc" style="margin-top:5px;">${{l.resumen_ia || ''}}</div>
                    <div class="meta">
                        <span class="tag">${{l.tipo || ''}}</span>
                        <span class="tag">${{l.estado || ''}}</span>
                        <span class="tag">${{(l.unidad || '').substring(0,25)}}</span>
                        <a href="${{l.url || '#'}}" target="_blank" class="btn">Ver Pliego en BAC</a>
                    </div>
                </div>`;
            }}).join('') || '<p>No hay licitaciones para esta fecha</p>';

            document.getElementById('otrosGrid').innerHTML = (d.sin_gastos || []).map(s => {{
                const anexosHtml = (s.anexos || []).length > 0 
                    ? `<div class="anexos-list" style="margin-top:8px;">
                        <small style="color:var(--text-secondary);">📎 Anexos:</small>
                        ${{(s.anexos || []).map(a => `<a href="${{a.url}}" target="_blank" class="tag" style="margin-left:4px;text-decoration:none;">${{(a.nombre || 'Anexo').substring(0,20)}}</a>`).join('')}}
                       </div>` 
                    : '';
                const resumenLargo = s.resumen_largo || s.sumario || s.text_snippet || 'Sin información adicional.';
                return `<div class="card" data-org="${{s.organismo}}">
                    <div class="desc"><strong>${{s.resumen_corto || s.nombre || ''}}</strong></div>
                    <div class="desc-long">${{resumenLargo}}${{anexosHtml}}</div>
                    <div class="meta">
                        <span class="tag">${{(s.organismo || '').substring(0,25)}}</span>
                        <button class="btn secondary" onclick="this.closest('.card').classList.toggle('expanded')">Ver más</button>
                        <a href="${{s.url || '#'}}" target="_blank" class="btn">PDF</a>
                    </div>
                </div>`;
            }}).join('') || '<p>No hay otras normas</p>';

            const allNorms = [...(d.gastos || []), ...(d.sin_gastos || [])];
            let anexosTabHtml = '';
            allNorms.forEach(n => {{
                (n.anexos || []).forEach(a => {{
                    const resumen = a.resumen || 'Anexo de: ' + (n.nombre || 'Norma').substring(0,50);
                    anexosTabHtml += `<div class="card">
                        <div class="desc"><strong>📄 ${{a.nombre || 'Anexo'}}</strong></div>
                        <div class="desc" style="margin-top:5px;color:var(--text-secondary);">${{resumen}}</div>
                        <div class="meta" style="margin-top:8px;">
                            <span class="tag">De: ${{(n.organismo || 'Desconocido').substring(0,20)}}</span>
                            <a href="${{a.url || '#'}}" target="_blank" class="btn">Descargar PDF</a>
                        </div>
                    </div>`;
                }});
            }});
            document.getElementById('anexosGrid').innerHTML = anexosTabHtml || '<p>No hay anexos para esta fecha</p>';

            if (document.getElementById('tab-stats').classList.contains('active')) initChart();
        }}
        
        function filterCards() {{
            const val = document.getElementById('filterOrganismo').value;
            document.querySelectorAll('.card').forEach(c => {{
                if(!val || !c.dataset.org) c.style.display = 'block';
                else c.style.display = c.dataset.org === val ? 'block' : 'none';
            }});
        }}
        
        function showTab(tab) {{
            document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-list li').forEach(t => t.classList.remove('active'));
            document.getElementById('tab-' + tab).classList.add('active');
            event.target.classList.add('active');
            if (tab === 'stats') initChart();
        }}
        
        function initChart() {{
            try {{
                const ctx = document.getElementById('statsChart').getContext('2d');
                const dates = Object.keys(allData).sort();
                const orgs = new Set();
                dates.forEach(d => (allData[d].gastos || []).forEach(g => orgs.add(g.organismo || 'Otros')));
                const orgList = Array.from(orgs).slice(0, 8);
                const colors = ['#e94560','#4ecca3','#ffc107','#00bcd4','#9c27b0','#ff5722','#2196f3','#8bc34a'];
                
                const datasets = orgList.map((org, i) => ({{
                    label: org.substring(0, 18),
                    data: dates.map(d => (allData[d].gastos || []).filter(x => x.organismo === org).length),
                    backgroundColor: colors[i % colors.length]
                }}));
                
                if (currentChart) currentChart.destroy();
                currentChart = new Chart(ctx, {{
                    type: 'bar',
                    data: {{ labels: dates.map(d => allData[d].fecha_display), datasets }},
                    options: {{ responsive: true, scales: {{ x: {{ stacked: true }}, y: {{ stacked: true }} }} }}
                }});
            }} catch(e) {{}}
        }}
        
        init();
    </script>
</body>
</html>'''
    
    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(html)
    
    print(f"✅ index.html generado")

if __name__ == "__main__":
    main()
