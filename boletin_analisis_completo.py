"""
Boletín Oficial - Análisis COMPLETO de Gastos v9 (Date Dropdown)
-------------------------------------------------
Features:
- Reintentos inteligentes GRANULARES
- Selector de Fechas (Dropdown)
- HTML mejorado
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

def clean_organismo(org):
    if not org: return "Otros"
    return org.strip().rstrip('-').rstrip().strip()

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

def get_ai_summary(client, prompt, system_prompt):
    try:
        result = client.predict(message=prompt, system_prompt=system_prompt, temperature=0.3, api_name="/chat")
        resp = result.split("**💬 Response:**")[1].strip() if "**💬 Response:**" in result else result
        return resp.strip()
    except:
        return None  # Return None on failure to track it

def process_norm(item):
    try:
        r = requests.get(item['url'], timeout=120)
        if r.status_code != 200:
            return False, item, f"HTTP {r.status_code}"
        with io.BytesIO(r.content) as f:
            reader = PdfReader(f)
            text = "".join([p.extract_text() + "\n" for p in reader.pages])
        amounts = extract_amounts(text)
        item['text_snippet'] = text[:800]
        if amounts:
            item['monto'] = max(amounts)
            item['monto_fmt'] = f"${item['monto']:,.2f}"
            item['todos_montos'] = len(amounts)
            item['tiene_gasto'] = True
        else:
            item['tiene_gasto'] = False
        return True, item, None
    except requests.exceptions.Timeout:
        return False, item, "timeout"
    except Exception as e:
        return False, item, str(e)[:50]

def process_anexo(anexo):
    try:
        r = requests.get(anexo['url'], timeout=120)
        if r.status_code != 200: return None
        with io.BytesIO(r.content) as f:
            reader = PdfReader(f)
            text = "".join([p.extract_text() + "\n" for p in reader.pages[:3]])
        return text[:1000]
    except: return None

def scrape_licitaciones(fecha_hoy):
    """Scrape Buenos Aires Compras for today's tenders. Returns (list, success_bool)"""
    print(f"\n🏛️ Scrapeando licitaciones de {fecha_hoy}...")
    licitaciones = []
    success = True
    
    try:
        options = Options()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        driver.get(BAC_URL)
        
        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.ID, "ctl00_CPH1_GridListaPliegos")))
        time.sleep(3)
        
        table = driver.find_element(By.ID, "ctl00_CPH1_GridListaPliegos")
        rows = table.find_elements(By.TAG_NAME, "tr")[1:]
        print(f"   Encontradas {len(rows)} licitaciones en la página")
        
        today_parts = fecha_hoy.split('/')
        today_str = f"{today_parts[0]}/{today_parts[1]}/{today_parts[2]}"
        
        for row in rows:
            try:
                cols = row.find_elements(By.TAG_NAME, "td")
                if len(cols) < 6: continue
                if today_str in cols[3].text.strip():
                    numero = cols[0].text.strip()
                    link_id = cols[0].find_element(By.TAG_NAME, "a").get_attribute("id")
                    licitaciones.append({
                        'numero': numero,
                        'nombre': cols[1].text.strip(),
                        'tipo': cols[2].text.strip(),
                        'fecha': cols[3].text.strip().split()[0],
                        'estado': cols[4].text.strip(),
                        'unidad': cols[5].text.strip(),
                        'link_id': link_id,
                        'url': f"https://www.buenosairescompras.gob.ar/GCBA/buscadorDePliegos.aspx?id={numero}"
                    })
            except: continue
        
        print(f"   Total hoy: {len(licitaciones)}")
        
        # Detail pages
        if licitaciones:
            print(f"📊 Extrayendo detalles...")
            for i, lic in enumerate(licitaciones[:20]):
                try:
                    link = driver.find_element(By.ID, lic['link_id'])
                    driver.execute_script("arguments[0].click();", link)
                    time.sleep(2)
                    
                    try:
                        monto_labels = driver.find_elements(By.TAG_NAME, "label")
                        for label in monto_labels:
                            if "Monto" in label.text:
                                val = driver.execute_script("return arguments[0].nextElementSibling;", label).text.strip()
                                amounts = extract_amounts(val)
                                if amounts:
                                    lic['monto'] = amounts[0]
                                    lic['monto_fmt'] = f"${lic['monto']:,.2f}"
                                break
                    except: pass
                    
                    lic['descripcion'] = driver.find_element(By.TAG_NAME, "body").text[:500]
                    driver.back()
                    time.sleep(1)
                except Exception as e:
                    print(f"   Error detalle {lic['numero']}: {e}")
                    driver.get(BAC_URL) # Reset to main list
                    time.sleep(2)
                    success = False # Partial failure
        
        driver.quit()
        return licitaciones, success

    except Exception as e:
        print(f"⚠️ Error scraping crítico: {e}")
        return [], False

def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    
    print("📥 Consultando API Boletín...")
    try:
        response = requests.get(API_URL, timeout=30)
        data = response.json()
    except Exception as e:
        print(f"❌ Error API Boletín: {e}")
        return

    boletin = data.get('boletin', {})
    fecha_raw = boletin.get('fecha_publicacion', '?')
    numero = boletin.get('numero', '?')
    
    try:
        fp = fecha_raw.split('/')
        fecha_iso = f"{fp[2]}-{fp[1].zfill(2)}-{fp[0].zfill(2)}"
    except:
        fecha_iso = datetime.now().strftime('%Y-%m-%d')
    
    data_file = os.path.join(DATA_DIR, f"{fecha_iso}.json")
    pending_file = os.path.join(DATA_DIR, f"{fecha_iso}_pendientes.json")
    
    print(f"📋 Boletín {numero} ({fecha_iso})")
    
    # --- LOAD STATE ---
    existing_data = None
    pending_state = {}
    
    if os.path.exists(data_file):
        with open(data_file, 'r', encoding='utf-8') as f:
            existing_data = json.load(f)
    
    if os.path.exists(pending_file):
        with open(pending_file, 'r', encoding='utf-8') as f:
            pending_state = json.load(f)
            
    # --- DETERMINE WORK ---
    
    if existing_data and not pending_state:
        print("✅ Día completo. Verificado (No se requiere acción).")
        regenerate_html()
        return

    # Initialize data if not exists
    if not existing_data:
        print("🆕 MODO INICIAL: Primer análisis del día")
        existing_data = {
            'fecha': fecha_iso, 'fecha_display': fecha_raw, 'numero_boletin': numero,
            'gastos': [], 'sin_gastos': [], 'licitaciones': [], 'organismos': []
        }
        # Gather all norms to process
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
            'anexos_pendientes': True,
            'resumenes_pendientes': True
        }

    # --- EXECUTION PHASES ---
    
    # 1. RETRY NORMS
    if pending_state.get('normas_pendientes'):
        pend = pending_state['normas_pendientes']
        print(f"🔄 Procesando {len(pend)} normas pendientes...")
        
        todavia_pendientes = []
        nuevos_gastos = []
        nuevos_sin_gastos = []
        
        for i, item in enumerate(pend):
            print(f"[{i+1}/{len(pend)}] {item['nombre'][:30]}...", end="", flush=True)
            success, processed, err = process_norm(item)
            if success:
                if processed['tiene_gasto']:
                    nuevos_gastos.append(processed)
                    print(" 💰")
                else:
                    nuevos_sin_gastos.append(processed)
                    print(" ✅")
            else:
                todavia_pendientes.append(item)
                print(" ❌")
            time.sleep(0.5)
            
        existing_data['gastos'].extend(nuevos_gastos)
        existing_data['sin_gastos'].extend(nuevos_sin_gastos)
        pending_state['normas_pendientes'] = todavia_pendientes
        
        # Save progress
        with open(data_file, 'w', encoding='utf-8') as f: json.dump(existing_data, f, indent=2, ensure_ascii=False)

    # 2. RETRY LICITACIONES
    if pending_state.get('licitaciones_necesarias'):
        lics, success = scrape_licitaciones(fecha_raw)
        if success:
            existing_data['licitaciones'] = lics
            pending_state['licitaciones_necesarias'] = False
            print("✅ Licitaciones completadas")
        else:
            print("⚠️ Licitaciones con errores (se reintentará)")
            pending_state['licitaciones_necesarias'] = True # Keep pending
        
        with open(data_file, 'w', encoding='utf-8') as f: json.dump(existing_data, f, indent=2, ensure_ascii=False)

    # 3. AI SUMMARIES & ANNEXES
    # Logic: iterate existing data, check for missing fields. 
    # Only run if marked pending OR if we just added new data
    
    if pending_state.get('resumenes_pendientes') or pending_state.get('anexos_pendientes'):
        print("\n🤖 Procesando IA y Anexos faltantes...")
        ia_errors = 0
        
        try:
            client = Client("amd/gpt-oss-120b-chatbot")
            
            CORTO = "Genera un TITULO BREVE (máximo 10 palabras) que diga QUÉ se compra. Estilo titular."
            LARGO = "Explica en 3 oraciones: El contexto, quién lo pide y para qué. No repitas el título."
            LIC_P = "Resume en 2 oraciones qué se licita."
            
            # Summarize Gastos
            for g in existing_data['gastos']:
                if 'resumen_corto' not in g or not g['resumen_corto']:
                    print(f"  AI Gasto: {g['nombre'][:20]}...", end="", flush=True)
                    prompt = f"Norma: {g['nombre']}\nOrganismo: {g['organismo']}\nMonto: {g.get('monto_fmt','')}\nTexto: {g.get('text_snippet','')[:500]}"
                    res = get_ai_summary(client, prompt, CORTO)
                    if res: 
                        g['resumen_corto'] = res
                        g['resumen_largo'] = get_ai_summary(client, prompt, LARGO) or ""
                        print(" ✅")
                    else:
                        ia_errors += 1
                        print(" ❌")
            
            # Summarize Other Norms
            for s in existing_data['sin_gastos'][:50]: # Limit to 50 for cost/speed
                if 'resumen_corto' not in s or not s['resumen_corto']:
                    print(f"  AI Norma: {s['nombre'][:20]}...", end="", flush=True)
                    res = get_ai_summary(client, f"Norma: {s['nombre']}\n{s.get('sumario','')}", CORTO)
                    if res:
                        s['resumen_corto'] = res
                        print(" ✅")
                    else: 
                        ia_errors += 1
                        print(" ❌")

            # Summarize Licitaciones
            for l in existing_data.get('licitaciones', []):
                if 'resumen_ia' not in l or not l['resumen_ia']:
                    print(f"  AI Lic: {l['numero']}...", end="", flush=True)
                    res = get_ai_summary(client, f"Lic: {l['nombre']}\n{l.get('descripcion','')[:300]}", LIC_P)
                    if res:
                        l['resumen_ia'] = res
                        print(" ✅")
                    else:
                        ia_errors += 1
                        print(" ❌")
            
            # Process Annexes
            print("📎 Verificando Anexos...")
            for n in existing_data['gastos'] + existing_data['sin_gastos']:
                for a in n.get('anexos', []):
                    if 'resumen' not in a: # Missing processing
                        txt = process_anexo(a)
                        if txt is not None: # Not None means download OK (empty string is valid text)
                            a['resumen'] = get_ai_summary(client, f"Anexo: {a['nombre']}\n{txt}", "Resume el anexo en 1 frase.") or "Ver PDF"
                        else:
                            # Download failed
                            ia_errors += 1 # Count as error to keep pending
            
            if ia_errors == 0:
                pending_state['resumenes_pendientes'] = False
                pending_state['anexos_pendientes'] = False
            else:
                print(f"⚠️ {ia_errors} fallos en IA/Anexos. Se reintentará.")
                
        except Exception as e:
            print(f"❌ Error IA Global: {e}")
            # Keep flags True
    
    # --- FINAL CLEANUP ---
    
    # Update Stats
    existing_data['total_normas'] = len(existing_data['gastos']) + len(existing_data['sin_gastos'])
    existing_data['organismos'] = sorted(list(set(g['organismo'] for g in existing_data['gastos'])))
    existing_data['gastos'].sort(key=lambda x: x.get('monto', 0), reverse=True)
    
    with open(data_file, 'w', encoding='utf-8') as f:
        json.dump(existing_data, f, indent=2, ensure_ascii=False)
        
    # Check if we can delete pending file
    has_pend = (
        len(pending_state.get('normas_pendientes', [])) > 0 or 
        pending_state.get('licitaciones_necesarias') or
        pending_state.get('resumenes_pendientes') or
        pending_state.get('anexos_pendientes')
    )
    
    if not has_pend:
        if os.path.exists(pending_file): os.remove(pending_file)
        print("🎉 TODO COMPLETADO EXITOSAMENTE")
    else:
        with open(pending_file, 'w', encoding='utf-8') as f:
            json.dump(pending_state, f, indent=2, ensure_ascii=False)
        print("💾 Estado pendiente guardado para próximo reintento")

    regenerate_html()

def regenerate_html():
    print("🌍 Regenerando index.html...")
    if not os.path.exists(DATA_DIR): return
    dates = sorted([f.replace('.json','') for f in os.listdir(DATA_DIR) if f.endswith('.json') and '_pendientes' not in f], reverse=True)
    if not dates: return
    
    all_data = {}
    for d in dates:
        try:
            with open(os.path.join(DATA_DIR, f"{d}.json"), 'r', encoding='utf-8') as f:
                all_data[d] = json.load(f)
        except: pass
        
    # HTML Template
    html = f'''<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Monitor de Gastos Públicos</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {{ --bg-primary: #1a1a2e; --bg-secondary: #16213e; --bg-tertiary: #0f3460; --text-primary: #eee; --text-secondary: #aaa; --accent: #e94560; --accent-hover: #ff6b6b; --success: #4ecca3; --warning: #ffc107; }}
        body.light-mode {{ --bg-primary: #f5f5f5; --bg-secondary: #fff; --bg-tertiary: #e0e0e0; --text-primary: #333; --text-secondary: #666; }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: var(--bg-primary); color: var(--text-primary); transition: all 0.3s; }}
        .container {{ display: flex; min-height: 100vh; }}
        .sidebar {{ width: 280px; background: var(--bg-secondary); padding: 20px; border-right: 1px solid var(--bg-tertiary); position: fixed; height: 100vh; overflow-y: auto; transition: transform 0.3s; }}
        .sidebar.collapsed {{ transform: translateX(-280px); }}
        .sidebar h2 {{ color: var(--accent); margin-bottom: 20px; }}
        .sidebar-section {{ margin-bottom: 25px; }}
        .sidebar-section h3 {{ color: var(--text-secondary); font-size: 0.8em; text-transform: uppercase; margin-bottom: 10px; }}
        
        /* DATE SELECTOR STYLES */
        .date-select {{ width: 100%; padding: 12px; border-radius: 8px; background: var(--accent); color: white; border: none; font-size: 1em; cursor: pointer; appearance: none; -webkit-appearance: none; text-align: center; font-weight: bold; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }}
        .date-select:hover {{ background: var(--accent-hover); }}
        .date-select:focus {{ outline: none; box-shadow: 0 0 0 2px var(--text-primary); }}
        .date-select option {{ background: var(--bg-secondary); color: var(--text-primary); text-align: left; padding: 10px; }}

        .tab-list {{ list-style: none; margin-top: 20px; }}
        .tab-list li {{ padding: 8px 12px; cursor: pointer; border-radius: 6px; margin-bottom: 4px; }}
        .tab-list li:hover {{ background: var(--bg-tertiary); }}
        .tab-list li.active {{ background: var(--bg-tertiary); border-left: 3px solid var(--accent); }}
        .theme-toggle {{ display: flex; align-items: center; gap: 10px; padding: 10px; background: var(--bg-tertiary); border-radius: 8px; cursor: pointer; margin-top: 15px; }}
        .theme-toggle-switch {{ width: 40px; height: 22px; background: #555; border-radius: 11px; position: relative; }}
        .theme-toggle-switch::after {{ content: ''; position: absolute; width: 18px; height: 18px; background: white; border-radius: 50%; top: 2px; left: 2px; transition: 0.3s; }}
        body.light-mode .theme-toggle-switch {{ background: var(--accent); }}
        body.light-mode .theme-toggle-switch::after {{ transform: translateX(18px); }}
        .toggle-btn {{ position: fixed; left: 280px; top: 20px; background: var(--accent); border: none; color: white; padding: 10px; border-radius: 0 6px 6px 0; cursor: pointer; z-index: 100; transition: left 0.3s; }}
        .toggle-btn.collapsed {{ left: 0; }}
        .main {{ margin-left: 280px; flex: 1; padding: 30px; transition: margin-left 0.3s; }}
        .main.expanded {{ margin-left: 0; }}
        .header {{ background: linear-gradient(135deg, var(--bg-tertiary), var(--bg-secondary)); padding: 25px; border-radius: 12px; margin-bottom: 25px; }}
        .header h1 {{ font-size: 1.8em; margin-bottom: 8px; }}
        .header-controls {{ display: flex; gap: 15px; align-items: center; margin-top: 15px; flex-wrap: wrap; }}
        .filter-select {{ padding: 8px 12px; border-radius: 6px; border: 1px solid var(--bg-tertiary); background: var(--bg-secondary); color: var(--text-primary); min-width: 200px; }}
        .stats {{ display: flex; gap: 15px; flex-wrap: wrap; }}
        .stat {{ background: rgba(233,69,96,0.2); padding: 10px 15px; border-radius: 8px; }}
        .stat-value {{ font-size: 1.3em; font-weight: bold; color: var(--accent); }}
        .stat-label {{ font-size: 0.75em; color: var(--text-secondary); }}
        .card-grid {{ display: grid; gap: 15px; }}
        .card {{ background: var(--bg-secondary); padding: 20px; border-radius: 10px; border-left: 4px solid var(--bg-tertiary); transition: all 0.2s; }}
        .card:hover {{ transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.3); }}
        .card.expensive {{ border-left-color: var(--accent); }}
        .card .amount {{ font-size: 1.3em; font-weight: bold; color: var(--success); margin-bottom: 8px; }}
        .card.expensive .amount {{ color: var(--accent); }}
        .card .desc {{ color: var(--text-secondary); line-height: 1.5; margin-bottom: 10px; }}
        .card .desc-long {{ display: none; margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--bg-tertiary); }}
        .card.expanded .desc-long {{ display: block; }}
        .card .meta {{ display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }}
        .tag {{ background: var(--bg-tertiary); padding: 4px 10px; border-radius: 4px; font-size: 0.8em; }}
        .btn {{ background: var(--accent); color: white; padding: 6px 12px; border-radius: 4px; text-decoration: none; font-size: 0.85em; cursor: pointer; border: none; }}
        .btn:hover {{ background: var(--accent-hover); }}
        .btn.secondary {{ background: var(--bg-tertiary); color: var(--text-primary); }}
        .anexos-list {{ margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--bg-tertiary); }}
        .anexo-link {{ display: inline-block; margin: 2px; padding: 4px 8px; background: var(--bg-tertiary); border-radius: 4px; font-size: 0.75em; color: var(--text-secondary); text-decoration: none; cursor: pointer; }}
        .anexo-link:hover {{ background: var(--accent); color: white; }}
        .tab-content {{ display: none; }}
        .tab-content.active {{ display: block; }}
        .chart-container {{ background: var(--bg-secondary); padding: 20px; border-radius: 12px; }}
        .chart-toggle {{ display: flex; gap: 10px; margin-bottom: 15px; }}
        .chart-toggle button {{ padding: 8px 16px; border-radius: 6px; border: none; cursor: pointer; background: var(--bg-tertiary); color: var(--text-primary); }}
        .chart-toggle button.active {{ background: var(--accent); color: white; }}
        .footer {{ text-align: center; padding: 30px; color: var(--text-secondary); }}
        .footer a {{ color: var(--accent); }}
    </style>
</head>
<body>
    <button class="toggle-btn" onclick="toggleSidebar()">☰</button>
    <div class="container">
        <aside class="sidebar" id="sidebar">
            <h2>🔍 Monitor</h2>
            <div class="sidebar-section">
                <h3>📅 Fecha</h3>
                <!-- DATE DROPDOWN -->
                <select id="dateSelect" class="date-select" onchange="loadDate(this.value)"></select>
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
            <div class="theme-toggle" onclick="toggleTheme()">
                <span>🌙</span><div class="theme-toggle-switch"></div><span>☀️</span>
            </div>
        </aside>
        <main class="main" id="main">
            <div class="header">
                <h1>Boletín N° <span id="numBoletin">-</span></h1>
                <p>Fecha: <span id="fechaDisplay">-</span></p>
                <div class="header-controls">
                    <select class="filter-select" id="filterOrganismo" onchange="filterByOrganismo()"></select>
                    <div class="stats">
                        <div class="stat"><div class="stat-value" id="statGastos">-</div><div class="stat-label">Gastos</div></div>
                        <div class="stat"><div class="stat-value" id="statLic">-</div><div class="stat-label">Licitaciones</div></div>
                        <div class="stat"><div class="stat-value" id="statAnexos">-</div><div class="stat-label">Anexos</div></div>
                    </div>
                </div>
            </div>
            
            <div class="tab-content active" id="tab-gastos"><div class="card-grid" id="gastosGrid"></div></div>
            <div class="tab-content" id="tab-licitaciones"><div class="card-grid" id="licitacionesGrid"></div></div>
            <div class="tab-content" id="tab-otros"><div class="card-grid" id="otrosGrid"></div></div>
            <div class="tab-content" id="tab-anexos"><div class="card-grid" id="anexosGrid"></div></div>
            <div class="tab-content" id="tab-stats">
                <div class="chart-container">
                    <div class="chart-toggle">
                        <button class="active" onclick="updateChart('count')">📊 Cantidad</button>
                        <button onclick="updateChart('amount')">💰 Monto</button>
                    </div>
                    <canvas id="statsChart" height="400"></canvas>
                </div>
            </div>
            
            <div class="footer"><a href="https://github.com/ignaciokairuz/Boletin_Oficial_AI">Boletin_Oficial_AI</a></div>
        </main>
    </div>
    
    <script>
        const allData = {json.dumps(all_data, ensure_ascii=False)};
        const sortedDates = Object.keys(allData).sort().reverse();
        let currentChart = null, chartMode = 'count';
        
        function init() {{
            const dateSelect = document.getElementById('dateSelect');
            sortedDates.forEach((date, i) => {{
                const opt = document.createElement('option');
                opt.value = date;
                opt.textContent = allData[date].fecha_display;
                dateSelect.appendChild(opt);
            }});
            
            // Populate filter
            const filter = document.getElementById('filterOrganismo');
            filter.innerHTML = '<option value="">🏛️ Todos los organismos</option>';
            const orgs = new Set();
            Object.values(allData).forEach(d => (d.organismos || []).forEach(o => orgs.add(o)));
            Array.from(orgs).sort().forEach(o => {{
                const opt = document.createElement('option');
                opt.value = o; opt.textContent = o.substring(0,40);
                filter.appendChild(opt);
            }});

            if(sortedDates.length > 0) loadDate(sortedDates[0]);
        }}

        function loadDate(date) {{
            const d = allData[date];
            if (!d) return;

            // Sync visual selector if needed
            document.getElementById('dateSelect').value = date;

            document.getElementById('numBoletin').textContent = d.numero_boletin;
            document.getElementById('fechaDisplay').textContent = d.fecha_display;
            document.getElementById('statGastos').textContent = d.gastos.length;
            document.getElementById('statLic').textContent = (d.licitaciones || []).length;
            
            // Calc total anexos
            let totalAnexos = 0;
            [...d.gastos, ...(d.sin_gastos||[])].forEach(n => totalAnexos += (n.anexos || []).length);
            document.getElementById('statAnexos').textContent = totalAnexos;

            // Render Gastos
            document.getElementById('gastosGrid').innerHTML = d.gastos.map(g => {{
                const expensive = g.monto > 100000000 ? 'expensive' : '';
                const anexos = g.anexos && g.anexos.length ? 
                    '<div class="anexos-list">📎 ' + g.anexos.map(a => `<span class="anexo-link" onclick="goToAnexo('${{a.nombre.replace(/[.-]/g,'_')}}')">${{a.nombre.substring(0,20)}}</span>`).join('') + '</div>' : '';
                return `<div class="card ${{expensive}}" data-organismo="${{g.organismo || ''}}">
                    <div class="amount">${{g.monto_fmt || '$0'}}</div>
                    <div class="desc"><strong>${{g.resumen_corto || g.sumario || ''}}</strong></div>
                    <div class="desc-long">${{g.resumen_largo || ''}}</div>
                    <div class="meta">
                        <span class="tag">${{(g.organismo || '').substring(0,30)}}</span>
                        <button class="btn secondary" onclick="this.closest('.card').classList.toggle('expanded')">Ver más</button>
                        <a href="${{g.url || '#'}}" target="_blank" class="btn">PDF</a>
                    </div>
                    ${{anexos}}
                </div>`;
            }}).join('');

            // Render Licitaciones
            document.getElementById('licitacionesGrid').innerHTML = (d.licitaciones || []).map(l => {{
                return `<div class="card">
                    <div class="amount">${{l.monto_fmt || 'Monto no disponible'}}</div>
                    <div class="desc"><strong>${{l.numero || ''}}</strong> - ${{l.nombre || ''}}</div>
                    <div class="desc">${{l.resumen_ia || ''}}</div>
                    <div class="meta">
                        <span class="tag">${{l.tipo || ''}}</span>
                        <span class="tag">${{(l.unidad || '').substring(0,25)}}</span>
                        <a href="${{l.url || '#'}}" target="_blank" class="btn">Ver en BAC</a>
                    </div>
                </div>`;
            }}).join('');

            // Render Otros
            document.getElementById('otrosGrid').innerHTML = (d.sin_gastos || []).map(s => {{
                return `<div class="card">
                    <div class="desc"><strong>${{s.nombre || ''}}</strong></div>
                    <div class="desc">${{s.resumen_corto || s.sumario || ''}}</div>
                    <div class="meta">
                        <span class="tag">${{(s.organismo || '').substring(0,30)}}</span>
                        <a href="${{s.url || '#'}}" target="_blank" class="btn">PDF</a>
                    </div>
                </div>`;
            }}).join('');

            // Render Anexos
            const allNorms = [...d.gastos, ...(d.sin_gastos || [])];
            let anexosHtml = '';
            allNorms.forEach(n => {{
                (n.anexos || []).forEach(a => {{
                    const aid = a.nombre.replace(/[.-]/g,'_');
                    anexosHtml += `<div class="card" id="anexo_${{aid}}">
                        <div class="desc"><strong>📄 ${{a.nombre}}</strong></div>
                        <div class="desc">De: ${{n.nombre || ''}}</div>
                        <div class="desc">${{a.resumen || ''}}</div>
                        <a href="${{a.url || '#'}}" target="_blank" class="btn">Descargar</a>
                    </div>`;
                }});
            }});
            document.getElementById('anexosGrid').innerHTML = anexosHtml;

            if (document.getElementById('tab-stats').classList.contains('active')) initChart();
        }}
        
        function toggleTheme() {{ document.body.classList.toggle('light-mode'); localStorage.setItem('theme', document.body.classList.contains('light-mode') ? 'light' : 'dark'); }}
        if (localStorage.getItem('theme') === 'light') document.body.classList.add('light-mode');
        
        function toggleSidebar() {{
            document.getElementById('sidebar').classList.toggle('collapsed');
            document.getElementById('main').classList.toggle('expanded');
            document.querySelector('.toggle-btn').classList.toggle('collapsed');
        }}
        
        function showTab(tab) {{
            document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-list li').forEach(t => t.classList.remove('active'));
            document.getElementById('tab-' + tab).classList.add('active');
            event.target.classList.add('active');
            if (tab === 'stats') initChart();
        }}
        
        function goToAnexo(id) {{
            showTab('anexos');
            setTimeout(() => {{ const el = document.getElementById('anexo_' + id); if (el) el.scrollIntoView({{ behavior: 'smooth' }}); }}, 100);
        }}
        
        function filterByOrganismo() {{
            const f = document.getElementById('filterOrganismo').value.toLowerCase();
            document.querySelectorAll('.card').forEach(c => {{ c.style.display = (!f || (c.dataset.organismo || '').toLowerCase().includes(f)) ? 'block' : 'none'; }});
        }}
        
        function initChart() {{
            const ctx = document.getElementById('statsChart').getContext('2d');
            const dates = Object.keys(allData).sort();
            const orgs = new Set();
            dates.forEach(d => (d.gastos||[]).forEach(g => orgs.add(g.organismo || 'Otros')));
            const orgList = Array.from(orgs).slice(0, 8);
            const colors = ['#e94560','#4ecca3','#ffc107','#00bcd4','#9c27b0','#ff5722','#2196f3','#8bc34a'];
            const datasets = orgList.map((org, i) => ({{
                label: org.substring(0, 20),
                data: dates.map(d => {{
                    const g = (allData[d].gastos||[]).filter(x => x.organismo === org);
                    return chartMode === 'count' ? g.length : g.reduce((s, x) => s + (x.monto || 0), 0);
                }}),
                backgroundColor: colors[i]
            }}));
            if (currentChart) currentChart.destroy();
            currentChart = new Chart(ctx, {{ type: 'bar', data: {{ labels: dates.map(d => allData[d].fecha_display), datasets }}, options: {{ responsive: true, scales: {{ x: {{ stacked: true }}, y: {{ stacked: true }} }} }} }});
        }}
        
        function updateChart(mode) {{
            chartMode = mode;
            document.querySelectorAll('.chart-toggle button').forEach(b => b.classList.remove('active'));
            event.target.classList.add('active');
            initChart();
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
