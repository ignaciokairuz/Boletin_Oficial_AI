"""
Boletín Oficial - Análisis COMPLETO de Gastos v6
-------------------------------------------------
Features:
- Reintentos inteligentes
- Resumen corto + detallado por gasto
- Resúmenes de todos los anexos
- Filtro por ministerio, gráfico stacked chart
- TAB LICITACIONES: scraping de Buenos Aires Compras
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
        return ""

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
        if r.status_code != 200: return ""
        with io.BytesIO(r.content) as f:
            reader = PdfReader(f)
            text = "".join([p.extract_text() + "\n" for p in reader.pages[:3]])
        return text[:1000]
    except: return ""

def scrape_licitaciones(fecha_hoy):
    """Scrape Buenos Aires Compras for today's tenders"""
    print(f"\n🏛️ Scrapeando licitaciones de {fecha_hoy}...")
    
    licitaciones = []
    
    try:
        # Setup headless Chrome
        options = Options()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        driver.get(BAC_URL)
        
        # Wait for table to load
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "table.grilla tbody tr"))
        )
        
        time.sleep(2)
        
        # Get all rows
        rows = driver.find_elements(By.CSS_SELECTOR, "table.grilla tbody tr")
        print(f"   Encontradas {len(rows)} licitaciones en la página")
        
        # Parse today's date format (dd/mm/yyyy)
        today_parts = fecha_hoy.split('/')
        today_str = f"{today_parts[0]}/{today_parts[1]}/{today_parts[2]}"
        
        for row in rows:
            try:
                cols = row.find_elements(By.TAG_NAME, "td")
                if len(cols) < 6: continue
                
                fecha_apertura = cols[3].text.strip()
                
                # Check if it's today's date
                if today_str in fecha_apertura:
                    numero = cols[0].text.strip()
                    nombre = cols[1].text.strip()
                    tipo = cols[2].text.strip()
                    estado = cols[4].text.strip()
                    unidad = cols[5].text.strip()
                    
                    # Get link to detail page
                    link_elem = cols[0].find_element(By.TAG_NAME, "a")
                    detail_url = link_elem.get_attribute("href")
                    
                    licitaciones.append({
                        'numero': numero,
                        'nombre': nombre,
                        'tipo': tipo,
                        'fecha': fecha_apertura.split()[0],  # Just the date
                        'estado': estado,
                        'unidad': unidad,
                        'url': detail_url
                    })
                    print(f"   ✓ {numero}: {nombre[:40]}...")
                    
            except Exception as e:
                continue
        
        # Get amounts from detail pages (top 20 to save time)
        print(f"\n📊 Extrayendo montos de {min(len(licitaciones), 20)} licitaciones...")
        
        for i, lic in enumerate(licitaciones[:20]):
            try:
                driver.get(lic['url'])
                time.sleep(2)
                
                # Look for amount in page
                page_text = driver.find_element(By.TAG_NAME, "body").text
                
                # Find amount patterns
                monto_match = re.search(r'Monto.*?(\$\s?[\d.,]+)', page_text, re.IGNORECASE)
                if monto_match:
                    monto_str = monto_match.group(1)
                    amounts = extract_amounts(monto_str)
                    if amounts:
                        lic['monto'] = amounts[0]
                        lic['monto_fmt'] = f"${lic['monto']:,.2f}"
                
                # Get description for AI summary
                lic['descripcion'] = page_text[:500]
                
                print(f"   [{i+1}/{min(len(licitaciones), 20)}] {lic['numero']}: {lic.get('monto_fmt', 'Sin monto')}")
                
            except Exception as e:
                print(f"   [{i+1}] Error: {str(e)[:30]}")
        
        driver.quit()
        
    except Exception as e:
        print(f"⚠️ Error en scraping: {e}")
    
    return licitaciones

def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    
    print("📥 Descargando boletín...")
    response = requests.get(API_URL)
    data = response.json()
    
    boletin = data.get('boletin', {})
    fecha_raw = boletin.get('fecha_publicacion', '?')
    numero = boletin.get('numero', '?')
    
    try:
        fecha_parts = fecha_raw.split('/')
        fecha_iso = f"{fecha_parts[2]}-{fecha_parts[1].zfill(2)}-{fecha_parts[0].zfill(2)}"
    except:
        fecha_iso = datetime.now().strftime('%Y-%m-%d')
    
    data_file = os.path.join(DATA_DIR, f"{fecha_iso}.json")
    pending_file = os.path.join(DATA_DIR, f"{fecha_iso}_pendientes.json")
    
    print(f"📋 Boletín N° {numero} - Fecha: {fecha_raw}")
    
    # Check if we need to do full analysis or just retry
    if os.path.exists(pending_file):
        # RETRY MODE
        print(f"\n🔄 MODO REINTENTO")
        with open(pending_file, 'r', encoding='utf-8') as f:
            pending_data = json.load(f)
        with open(data_file, 'r', encoding='utf-8') as f:
            existing_data = json.load(f)
        
        pendientes = pending_data.get('pendientes', [])
        nuevos_gastos, nuevos_sin_gastos, aun_pendientes = [], [], []
        
        for i, item in enumerate(pendientes):
            print(f"[{i+1}/{len(pendientes)}] {item['nombre'][:40]}...", end="", flush=True)
            success, processed_item, error = process_norm(item)
            if success:
                if processed_item.get('tiene_gasto'):
                    nuevos_gastos.append(processed_item)
                    print(f" ✅")
                else:
                    nuevos_sin_gastos.append(processed_item)
                    print(" ✅")
            else:
                aun_pendientes.append(item)
                print(f" ❌")
            time.sleep(0.5)
        
        existing_data['gastos'].extend(nuevos_gastos)
        existing_data['sin_gastos'].extend(nuevos_sin_gastos)
        existing_data['gastos'].sort(key=lambda x: x.get('monto', 0), reverse=True)
        
        with open(data_file, 'w', encoding='utf-8') as f:
            json.dump(existing_data, f, indent=2, ensure_ascii=False)
        
        if aun_pendientes:
            pending_data['pendientes'] = aun_pendientes
            with open(pending_file, 'w', encoding='utf-8') as f:
                json.dump(pending_data, f, indent=2, ensure_ascii=False)
        else:
            os.remove(pending_file)
            print("✅ Todas procesadas")
        
    elif not os.path.exists(data_file):
        # NORMAL MODE - First run
        print(f"\n🆕 MODO NORMAL")
        
        all_norms = []
        normas_root = data.get('normas', {}).get('normas', {})
        
        for poder, tipos in normas_root.items():
            for tipo, organismos in tipos.items():
                for organismo, lista in organismos.items():
                    for item in lista:
                        anexos = [{'nombre': a.get('nombre_anexo', ''), 'url': a.get('filenet_firmado', '')} for a in item.get('anexos', [])]
                        all_norms.append({
                            'nombre': item.get('nombre'),
                            'sumario': item.get('sumario'),
                            'url': item.get('url_norma'),
                            'tipo': tipo,
                            'organismo': organismo,
                            'anexos': anexos
                        })
        
        total_anexos = sum(len(n.get('anexos', [])) for n in all_norms)
        print(f"📊 Normas: {len(all_norms)}, Anexos: {total_anexos}")
        
        gastos, sin_gastos, pendientes = [], [], []
        
        for i, item in enumerate(all_norms):
            print(f"[{i+1}/{len(all_norms)}] {item['nombre'][:45]}...", end="", flush=True)
            success, processed_item, error = process_norm(item)
            if success:
                if processed_item.get('tiene_gasto'):
                    gastos.append(processed_item)
                    print(f" 💰")
                else:
                    sin_gastos.append(processed_item)
                    print("")
            else:
                pendientes.append(item)
                print(f" ❌")
            time.sleep(0.3)
        
        gastos.sort(key=lambda x: x.get('monto', 0), reverse=True)
        
        # === SCRAPE LICITACIONES ===
        licitaciones = scrape_licitaciones(fecha_raw)
        
        # === AI SUMMARIES ===
        print(f"\n🤖 Generando resúmenes IA...")
        try:
            client = Client("amd/gpt-oss-120b-chatbot")
            
            CORTO = "Explica en 1 oración de 15-25 palabras el propósito del gasto. Solo la oración."
            LARGO = "Explica en 4 oraciones simples: qué se compra, para qué, quién lo ejecuta, y el monto. Lenguaje ciudadano."
            LIC_PROMPT = "Explica en 2 oraciones simples qué se está licitando y para qué sirve. Lenguaje ciudadano."
            
            for i, g in enumerate(gastos[:50]):
                print(f"  [Gasto {i+1}/50]", end=" ", flush=True)
                prompt = f"Norma: {g['nombre']}\nOrganismo: {g['organismo']}\nMonto: {g.get('monto_fmt','')}\nTexto: {g.get('text_snippet','')[:500]}"
                g['resumen_corto'] = get_ai_summary(client, prompt, CORTO) or g.get('sumario', '')
                g['resumen_largo'] = get_ai_summary(client, prompt, LARGO) or g.get('sumario', '')
                print("✓")
            
            for i, s in enumerate(sin_gastos[:30]):
                print(f"  [Norma {i+1}/30]", end=" ", flush=True)
                s['resumen_corto'] = get_ai_summary(client, f"Norma: {s['nombre']}\n{s.get('sumario','')}", CORTO) or s.get('sumario', '')
                print("✓")
            
            # Licitaciones summaries
            for i, lic in enumerate(licitaciones[:20]):
                print(f"  [Lic {i+1}/20]", end=" ", flush=True)
                prompt = f"Licitación: {lic['nombre']}\nUnidad: {lic['unidad']}\n{lic.get('descripcion','')[:300]}"
                lic['resumen_ia'] = get_ai_summary(client, prompt, LIC_PROMPT) or lic['nombre']
                print("✓")
            
            # Anexos
            print(f"\n📎 Procesando anexos...")
            for norm in gastos + sin_gastos:
                for anexo in norm.get('anexos', []):
                    texto = process_anexo(anexo)
                    if texto:
                        anexo['resumen'] = get_ai_summary(client, f"Anexo: {anexo['nombre']}\n{texto}", "Resume en 2 oraciones qué contiene este anexo.")
                    else:
                        anexo['resumen'] = ""
                        
        except Exception as e:
            print(f"⚠️ Error IA: {e}")
        
        organismos_unicos = sorted(set(g.get('organismo', '') for g in gastos if g.get('organismo')))
        
        day_data = {
            'fecha': fecha_iso,
            'fecha_display': fecha_raw,
            'numero_boletin': numero,
            'total_normas': len(all_norms),
            'total_anexos': total_anexos,
            'organismos': organismos_unicos,
            'gastos': gastos,
            'sin_gastos': sin_gastos[:50],
            'licitaciones': licitaciones,
            'errores': len(pendientes)
        }
        
        with open(data_file, 'w', encoding='utf-8') as f:
            json.dump(day_data, f, indent=2, ensure_ascii=False)
        
        if pendientes:
            with open(pending_file, 'w', encoding='utf-8') as f:
                json.dump({'fecha': fecha_iso, 'pendientes': pendientes}, f, indent=2, ensure_ascii=False)
        
        print(f"\n{'='*50}")
        print(f"📊 Gastos: {len(gastos)} | Licitaciones: {len(licitaciones)}")
    
    else:
        print(f"✅ Ya existe datos para {fecha_iso}")
    
    regenerate_html()

def regenerate_html():
    print("🌍 Regenerando index.html...")
    
    if not os.path.exists(DATA_DIR): return
    
    dates = sorted([f.replace('.json', '') for f in os.listdir(DATA_DIR) 
                   if f.endswith('.json') and '_pendientes' not in f], reverse=True)
    if not dates: return
    
    all_data = {}
    for date in dates:
        with open(os.path.join(DATA_DIR, f"{date}.json"), 'r', encoding='utf-8') as f:
            all_data[date] = json.load(f)
    
    latest = all_data[dates[0]]
    
    all_organismos = set()
    for d in all_data.values():
        all_organismos.update(d.get('organismos', []))
    all_organismos = sorted(all_organismos)
    
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
        .date-list, .tab-list {{ list-style: none; }}
        .date-list li, .tab-list li {{ padding: 8px 12px; cursor: pointer; border-radius: 6px; margin-bottom: 4px; }}
        .date-list li:hover, .tab-list li:hover {{ background: var(--bg-tertiary); }}
        .date-list li.active {{ background: var(--accent); color: white; }}
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
                <ul class="date-list" id="dateList">
'''
    
    for i, date in enumerate(dates):
        d = all_data[date]
        active = "active" if i == 0 else ""
        html += f'                    <li class="{active}" onclick="loadDate(\'{date}\')">{d["fecha_display"]}</li>\n'
    
    licitaciones_count = len(latest.get('licitaciones', []))
    
    html += f'''                </ul>
            </div>
            <div class="sidebar-section">
                <h3>📋 Vista</h3>
                <ul class="tab-list">
                    <li class="active" onclick="showTab('gastos')">💰 Gastos</li>
                    <li onclick="showTab('licitaciones')">🏛️ Licitaciones ({licitaciones_count})</li>
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
                <h1>Boletín N° <span id="numBoletin">{latest['numero_boletin']}</span></h1>
                <p>Fecha: <span id="fechaDisplay">{latest['fecha_display']}</span></p>
                <div class="header-controls">
                    <select class="filter-select" id="filterOrganismo" onchange="filterByOrganismo()">
                        <option value="">🏛️ Todos los organismos</option>
'''
    
    for org in all_organismos:
        html += f'                        <option value="{org}">{org[:40]}</option>\n'
    
    html += f'''                    </select>
                    <div class="stats">
                        <div class="stat"><div class="stat-value" id="statGastos">{len(latest['gastos'])}</div><div class="stat-label">Gastos</div></div>
                        <div class="stat"><div class="stat-value" id="statLic">{licitaciones_count}</div><div class="stat-label">Licitaciones</div></div>
                        <div class="stat"><div class="stat-value" id="statAnexos">{latest.get('total_anexos', 0)}</div><div class="stat-label">Anexos</div></div>
                    </div>
                </div>
            </div>
            
            <div class="tab-content active" id="tab-gastos">
                <div class="card-grid" id="gastosGrid">
'''
    
    for g in latest['gastos']:
        expensive = "expensive" if g.get('monto', 0) > 100_000_000 else ""
        anexos_html = ""
        if g.get('anexos'):
            anexos_html = '<div class="anexos-list">📎 ' + ''.join([f'<span class="anexo-link" onclick="goToAnexo(\'{a["nombre"].replace(".", "_").replace("-", "_")}\')">{a["nombre"][:20]}</span>' for a in g['anexos']]) + '</div>'
        
        html += f'''                    <div class="card {expensive}" data-organismo="{g.get('organismo', '')}">
                        <div class="amount">{g.get('monto_fmt', '$0')}</div>
                        <div class="desc">{g.get('resumen_corto', g.get('sumario', ''))}</div>
                        <div class="desc-long">{g.get('resumen_largo', '')}</div>
                        <div class="meta">
                            <span class="tag">{g.get('organismo', '')[:30]}</span>
                            <button class="btn secondary" onclick="this.closest('.card').classList.toggle('expanded')">Ver más</button>
                            <a href="{g.get('url', '#')}" target="_blank" class="btn">PDF</a>
                        </div>
                        {anexos_html}
                    </div>
'''
    
    # Licitaciones tab
    html += '''                </div>
            </div>
            
            <div class="tab-content" id="tab-licitaciones">
                <div class="card-grid" id="licitacionesGrid">
'''
    
    for lic in latest.get('licitaciones', []):
        monto_fmt = lic.get('monto_fmt', 'Monto no disponible')
        html += f'''                    <div class="card">
                        <div class="amount">{monto_fmt}</div>
                        <div class="desc"><strong>{lic.get('numero', '')}</strong> - {lic.get('nombre', '')}</div>
                        <div class="desc">{lic.get('resumen_ia', '')}</div>
                        <div class="meta">
                            <span class="tag">{lic.get('tipo', '')}</span>
                            <span class="tag">{lic.get('unidad', '')[:25]}</span>
                            <a href="{lic.get('url', '#')}" target="_blank" class="btn">Ver en BAC</a>
                        </div>
                    </div>
'''
    
    # Other tabs (simplified for length)
    html += '''                </div>
            </div>
            
            <div class="tab-content" id="tab-otros">
                <div class="card-grid" id="otrosGrid">
'''
    
    for s in latest.get('sin_gastos', []):
        html += f'''                    <div class="card">
                        <div class="desc"><strong>{s.get('nombre', '')}</strong></div>
                        <div class="desc">{s.get('resumen_corto', s.get('sumario', ''))}</div>
                        <div class="meta">
                            <span class="tag">{s.get('organismo', '')[:30]}</span>
                            <a href="{s.get('url', '#')}" target="_blank" class="btn">PDF</a>
                        </div>
                    </div>
'''
    
    html += '''                </div>
            </div>
            
            <div class="tab-content" id="tab-anexos">
                <div class="card-grid" id="anexosGrid">
'''
    
    for norm in latest['gastos'] + latest.get('sin_gastos', []):
        for anx in norm.get('anexos', []):
            aid = anx['nombre'].replace('.', '_').replace('-', '_')
            html += f'''                    <div class="card" id="anexo_{aid}">
                        <div class="desc"><strong>📄 {anx['nombre']}</strong></div>
                        <div class="desc">De: {norm.get('nombre', '')}</div>
                        <div class="desc">{anx.get('resumen', '')}</div>
                        <a href="{anx.get('url', '#')}" target="_blank" class="btn">Descargar</a>
                    </div>
'''
    
    html += f'''                </div>
            </div>
            
            <div class="tab-content" id="tab-stats">
                <div class="chart-container">
                    <div class="chart-toggle">
                        <button class="active" onclick="updateChart('count')">📊 Cantidad</button>
                        <button onclick="updateChart('amount')">💰 Monto</button>
                    </div>
                    <canvas id="statsChart" height="400"></canvas>
                </div>
            </div>
            
            <div class="footer">
                <a href="https://github.com/ignaciokairuz/Boletin_Oficial_AI">Boletin_Oficial_AI</a>
            </div>
        </main>
    </div>
    
    <script>
        const allData = {json.dumps(all_data, ensure_ascii=False)};
        let currentChart = null, chartMode = 'count';
        
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
            document.querySelectorAll('.tab-list li')[3].classList.add('active');
            setTimeout(() => {{ const el = document.getElementById('anexo_' + id); if (el) el.scrollIntoView({{ behavior: 'smooth' }}); }}, 100);
        }}
        
        function filterByOrganismo() {{
            const f = document.getElementById('filterOrganismo').value.toLowerCase();
            document.querySelectorAll('.card').forEach(c => {{ c.style.display = (!f || (c.dataset.organismo || '').toLowerCase().includes(f)) ? 'block' : 'none'; }});
        }}
        
        function loadDate(date) {{
            const d = allData[date]; if (!d) return;
            document.getElementById('numBoletin').textContent = d.numero_boletin;
            document.getElementById('fechaDisplay').textContent = d.fecha_display;
            document.getElementById('statGastos').textContent = d.gastos.length;
            document.getElementById('statLic').textContent = (d.licitaciones || []).length;
            document.querySelectorAll('.date-list li').forEach(li => li.classList.remove('active'));
            event.target.classList.add('active');
        }}
        
        function initChart() {{
            const ctx = document.getElementById('statsChart').getContext('2d');
            const dates = Object.keys(allData).sort();
            const orgs = new Set();
            dates.forEach(d => allData[d].gastos.forEach(g => orgs.add(g.organismo || 'Otros')));
            const orgList = Array.from(orgs).slice(0, 8);
            const colors = ['#e94560','#4ecca3','#ffc107','#00bcd4','#9c27b0','#ff5722','#2196f3','#8bc34a'];
            const datasets = orgList.map((org, i) => ({{
                label: org.substring(0, 20),
                data: dates.map(d => {{
                    const g = allData[d].gastos.filter(x => x.organismo === org);
                    return chartMode === 'count' ? g.length : g.reduce((s, x) => s + (x.monto || 0), 0);
                }}),
                backgroundColor: colors[i]
            }}));
            if (currentChart) currentChart.destroy();
            currentChart = new Chart(ctx, {{ type: 'bar', data: {{ labels: dates, datasets }}, options: {{ responsive: true, scales: {{ x: {{ stacked: true }}, y: {{ stacked: true }} }} }} }});
        }}
        
        function updateChart(mode) {{
            chartMode = mode;
            document.querySelectorAll('.chart-toggle button').forEach(b => b.classList.remove('active'));
            event.target.classList.add('active');
            initChart();
        }}
    </script>
</body>
</html>'''
    
    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(html)
    
    print(f"✅ index.html generado")

if __name__ == "__main__":
    main()
