"""
Boletín Oficial - Análisis COMPLETO de Gastos v5
-------------------------------------------------
Features:
- Reintentos inteligentes (solo reprocesar fallidas)
- Resumen corto + detallado (4 oraciones) por gasto
- Resúmenes de TODOS los anexos
- Filtro por ministerio, gráfico stacked chart
- Modo día/noche, sidebar con pestañas
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

API_URL = "https://api-restboletinoficial.buenosaires.gob.ar/obtenerBoletin/0/true"
AMOUNT_REGEX = r'\$\s?(\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?)'
DATA_DIR = "datos"

def extract_amounts(text):
    if not text: return []
    matches = re.finditer(AMOUNT_REGEX, text)
    amounts = []
    for m in matches:
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
    """Descarga y extrae texto de un anexo PDF"""
    try:
        r = requests.get(anexo['url'], timeout=120)
        if r.status_code != 200:
            return ""
        with io.BytesIO(r.content) as f:
            reader = PdfReader(f)
            text = "".join([p.extract_text() + "\n" for p in reader.pages[:3]])  # Solo primeras 3 páginas
        return text[:1000]
    except:
        return ""

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
    
    # === MODO REINTENTO ===
    if os.path.exists(pending_file):
        print(f"\n🔄 MODO REINTENTO")
        with open(pending_file, 'r', encoding='utf-8') as f:
            pending_data = json.load(f)
        with open(data_file, 'r', encoding='utf-8') as f:
            existing_data = json.load(f)
        
        pendientes = pending_data.get('pendientes', [])
        print(f"   {len(pendientes)} normas pendientes\n")
        
        nuevos_gastos, nuevos_sin_gastos, aun_pendientes = [], [], []
        
        for i, item in enumerate(pendientes):
            print(f"[{i+1}/{len(pendientes)}] {item['nombre'][:40]}...", end="", flush=True)
            success, processed_item, error = process_norm(item)
            if success:
                if processed_item.get('tiene_gasto'):
                    nuevos_gastos.append(processed_item)
                    print(f" ✅ {processed_item.get('monto_fmt', '')}")
                else:
                    nuevos_sin_gastos.append(processed_item)
                    print(" ✅")
            else:
                item['ultimo_error'] = error
                aun_pendientes.append(item)
                print(f" ❌ {error}")
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
            print(f"\n⚠️ Aún quedan {len(aun_pendientes)} pendientes")
        else:
            os.remove(pending_file)
            print(f"\n✅ Todas las normas procesadas")
        
    # === MODO NORMAL ===
    elif not os.path.exists(data_file):
        print(f"\n🆕 MODO NORMAL: Primera ejecución del día")
        
        all_norms = []
        normas_root = data.get('normas', {}).get('normas', {})
        
        for poder, tipos in normas_root.items():
            for tipo, organismos in tipos.items():
                for organismo, lista in organismos.items():
                    for item in lista:
                        anexos_raw = item.get('anexos', [])
                        anexos = [{'nombre': a.get('nombre_anexo', ''), 'url': a.get('filenet_firmado', '')} for a in anexos_raw]
                        all_norms.append({
                            'nombre': item.get('nombre'),
                            'sumario': item.get('sumario'),
                            'url': item.get('url_norma'),
                            'tipo': tipo,
                            'organismo': organismo,
                            'anexos': anexos
                        })
        
        print(f"📊 Total normas: {len(all_norms)}")
        total_anexos = sum(len(n.get('anexos', [])) for n in all_norms)
        print(f"📎 Total anexos: {total_anexos}\n")
        
        gastos, sin_gastos, pendientes = [], [], []
        
        for i, item in enumerate(all_norms):
            print(f"[{i+1}/{len(all_norms)}] {item['nombre'][:45]}...", end="", flush=True)
            success, processed_item, error = process_norm(item)
            if success:
                if processed_item.get('tiene_gasto'):
                    gastos.append(processed_item)
                    print(f" 💰 {processed_item.get('monto_fmt', '')}")
                else:
                    sin_gastos.append(processed_item)
                    print(" (sin $)")
            else:
                item['ultimo_error'] = error
                pendientes.append(item)
                print(f" ❌ {error}")
            time.sleep(0.3)
        
        gastos.sort(key=lambda x: x.get('monto', 0), reverse=True)
        
        # === GENERAR RESÚMENES IA ===
        print(f"\n🤖 Generando resúmenes con IA...")
        try:
            client = Client("amd/gpt-oss-120b-chatbot")
            
            CORTO = "Eres un analista de gastos públicos. Explica en UNA oración de 15-25 palabras el propósito del gasto. Solo la oración, sin formato."
            LARGO = """Eres un comunicador que explica gastos públicos a ciudadanos comunes.
Escribe EXACTAMENTE 4 oraciones cortas y claras explicando:
1. Qué se está comprando o contratando
2. Para qué sirve o quién se beneficia
3. Qué organismo lo ejecuta
4. Cualquier detalle relevante del monto
Usa lenguaje simple, evita jerga técnica. Solo las 4 oraciones, sin formato."""
            
            ANEXO_PROMPT = """Eres un analista que resume documentos públicos para ciudadanos.
Resume el contenido de este anexo en 2-3 oraciones simples. Explica qué contiene y para qué sirve.
Solo las oraciones, sin formato adicional."""
            
            # Resúmenes de gastos (corto + largo)
            for i, g in enumerate(gastos[:50]):
                print(f"  [Gasto {i+1}/50] {g['nombre'][:30]}...", end=" ", flush=True)
                prompt = f"Norma: {g['nombre']}\nOrganismo: {g['organismo']}\nMonto: {g.get('monto_fmt','')}\nSumario: {g.get('sumario','')}\nTexto: {g.get('text_snippet','')[:500]}"
                g['resumen_corto'] = get_ai_summary(client, prompt, CORTO) or g.get('sumario', '')
                g['resumen_largo'] = get_ai_summary(client, prompt, LARGO) or g.get('sumario', '')
                print("✓")
            
            # Resúmenes de otras normas
            for i, s in enumerate(sin_gastos[:30]):
                print(f"  [Otro {i+1}/30] {s['nombre'][:30]}...", end=" ", flush=True)
                prompt = f"Norma: {s['nombre']}\nOrganismo: {s['organismo']}\nSumario: {s.get('sumario','')}"
                s['resumen_corto'] = get_ai_summary(client, prompt, CORTO) or s.get('sumario', '')
                print("✓")
            
            # Resúmenes de TODOS los anexos
            print(f"\n📎 Procesando {total_anexos} anexos...")
            anexo_count = 0
            for norm in gastos + sin_gastos:
                for anexo in norm.get('anexos', []):
                    anexo_count += 1
                    print(f"  [Anexo {anexo_count}/{total_anexos}] {anexo['nombre'][:30]}...", end=" ", flush=True)
                    texto = process_anexo(anexo)
                    if texto:
                        prompt = f"Anexo: {anexo['nombre']}\nContenido: {texto}"
                        anexo['resumen'] = get_ai_summary(client, prompt, ANEXO_PROMPT)
                        print("✓")
                    else:
                        anexo['resumen'] = "No se pudo extraer contenido del PDF"
                        print("⚠️")
                    time.sleep(0.2)
                    
        except Exception as e:
            print(f"⚠️ Error IA: {e}")
            for g in gastos:
                g['resumen_corto'] = g.get('sumario', '')
                g['resumen_largo'] = g.get('sumario', '')
            for s in sin_gastos:
                s['resumen_corto'] = s.get('sumario', '')
        
        # Extraer lista de organismos únicos
        organismos_unicos = sorted(set(g.get('organismo', '') for g in gastos if g.get('organismo')))
        
        # Guardar datos
        day_data = {
            'fecha': fecha_iso,
            'fecha_display': fecha_raw,
            'numero_boletin': numero,
            'total_normas': len(all_norms),
            'total_anexos': total_anexos,
            'organismos': organismos_unicos,
            'gastos': gastos,
            'sin_gastos': sin_gastos[:50],
            'errores': len(pendientes)
        }
        
        with open(data_file, 'w', encoding='utf-8') as f:
            json.dump(day_data, f, indent=2, ensure_ascii=False)
        
        if pendientes:
            with open(pending_file, 'w', encoding='utf-8') as f:
                json.dump({'fecha': fecha_iso, 'pendientes': pendientes}, f, indent=2, ensure_ascii=False)
            print(f"\n⚠️ {len(pendientes)} normas para reintentar")
        
        print(f"\n{'='*60}")
        print(f"📊 RESUMEN - Boletín N° {numero}")
        print(f"{'='*60}")
        print(f"  ✓ Exitosas:   {len(gastos) + len(sin_gastos)}")
        print(f"  ✗ Pendientes: {len(pendientes)}")
        print(f"  💰 Con gasto: {len(gastos)}")
        print(f"  📎 Anexos:    {total_anexos}")
    
    else:
        print(f"✅ Ya existe datos completos para {fecha_iso}")
    
    regenerate_html()

def regenerate_html():
    print("🌍 Regenerando index.html...")
    
    if not os.path.exists(DATA_DIR):
        print("⚠️ No hay datos")
        return
    
    dates = sorted([f.replace('.json', '') for f in os.listdir(DATA_DIR) 
                   if f.endswith('.json') and '_pendientes' not in f], reverse=True)
    if not dates:
        print("⚠️ No hay datos")
        return
    
    all_data = {}
    for date in dates:
        with open(os.path.join(DATA_DIR, f"{date}.json"), 'r', encoding='utf-8') as f:
            all_data[date] = json.load(f)
    
    latest = all_data[dates[0]]
    
    # Pending check
    pending_file = os.path.join(DATA_DIR, f"{dates[0]}_pendientes.json")
    pending_count = 0
    if os.path.exists(pending_file):
        with open(pending_file, 'r', encoding='utf-8') as f:
            pending_count = len(json.load(f).get('pendientes', []))
    
    # Collect all organismos across all dates
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
        :root {{
            --bg-primary: #1a1a2e; --bg-secondary: #16213e; --bg-tertiary: #0f3460;
            --text-primary: #eee; --text-secondary: #aaa;
            --accent: #e94560; --accent-hover: #ff6b6b; --success: #4ecca3; --warning: #ffc107;
        }}
        body.light-mode {{
            --bg-primary: #f5f5f5; --bg-secondary: #ffffff; --bg-tertiary: #e0e0e0;
            --text-primary: #333; --text-secondary: #666;
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: var(--bg-primary); color: var(--text-primary); transition: all 0.3s; }}
        .container {{ display: flex; min-height: 100vh; }}
        .sidebar {{ width: 280px; background: var(--bg-secondary); padding: 20px; border-right: 1px solid var(--bg-tertiary); position: fixed; height: 100vh; overflow-y: auto; transition: transform 0.3s; }}
        .sidebar.collapsed {{ transform: translateX(-280px); }}
        .sidebar h2 {{ color: var(--accent); margin-bottom: 20px; font-size: 1.2em; }}
        .sidebar-section {{ margin-bottom: 25px; }}
        .sidebar-section h3 {{ color: var(--text-secondary); font-size: 0.8em; text-transform: uppercase; margin-bottom: 10px; }}
        .date-list, .tab-list {{ list-style: none; }}
        .date-list li, .tab-list li {{ padding: 8px 12px; cursor: pointer; border-radius: 6px; margin-bottom: 4px; transition: background 0.2s; }}
        .date-list li:hover, .tab-list li:hover {{ background: var(--bg-tertiary); }}
        .date-list li.active {{ background: var(--accent); color: white; }}
        .tab-list li.active {{ background: var(--bg-tertiary); border-left: 3px solid var(--accent); }}
        .theme-toggle {{ display: flex; align-items: center; gap: 10px; padding: 10px; background: var(--bg-tertiary); border-radius: 8px; cursor: pointer; margin-top: 15px; }}
        .theme-toggle-switch {{ width: 40px; height: 22px; background: #555; border-radius: 11px; position: relative; }}
        .theme-toggle-switch::after {{ content: ''; position: absolute; width: 18px; height: 18px; background: white; border-radius: 50%; top: 2px; left: 2px; transition: transform 0.3s; }}
        body.light-mode .theme-toggle-switch {{ background: var(--accent); }}
        body.light-mode .theme-toggle-switch::after {{ transform: translateX(18px); }}
        .toggle-btn {{ position: fixed; left: 280px; top: 20px; background: var(--accent); border: none; color: white; padding: 10px; border-radius: 0 6px 6px 0; cursor: pointer; z-index: 100; transition: left 0.3s; }}
        .toggle-btn.collapsed {{ left: 0; }}
        .main {{ margin-left: 280px; flex: 1; padding: 30px; transition: margin-left 0.3s; }}
        .main.expanded {{ margin-left: 0; }}
        .header {{ background: linear-gradient(135deg, var(--bg-tertiary), var(--bg-secondary)); padding: 25px; border-radius: 12px; margin-bottom: 25px; }}
        .header h1 {{ font-size: 1.8em; margin-bottom: 8px; }}
        .header-controls {{ display: flex; gap: 15px; align-items: center; margin-top: 15px; flex-wrap: wrap; }}
        .filter-select {{ padding: 8px 12px; border-radius: 6px; border: 1px solid var(--bg-tertiary); background: var(--bg-secondary); color: var(--text-primary); font-size: 0.9em; min-width: 200px; }}
        .stats {{ display: flex; gap: 15px; flex-wrap: wrap; }}
        .stat {{ background: rgba(233,69,96,0.2); padding: 10px 15px; border-radius: 8px; }}
        .stat-value {{ font-size: 1.3em; font-weight: bold; color: var(--accent); }}
        .stat-label {{ font-size: 0.75em; color: var(--text-secondary); }}
        .stat.warning {{ background: rgba(255,193,7,0.2); }}
        .stat.warning .stat-value {{ color: var(--warning); }}
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
        .anexo-link {{ display: inline-block; margin-right: 8px; margin-bottom: 5px; padding: 4px 8px; background: var(--bg-tertiary); border-radius: 4px; font-size: 0.75em; color: var(--text-secondary); text-decoration: none; cursor: pointer; }}
        .anexo-link:hover {{ background: var(--accent); color: white; }}
        .tab-content {{ display: none; }}
        .tab-content.active {{ display: block; }}
        .chart-container {{ background: var(--bg-secondary); padding: 20px; border-radius: 12px; }}
        .chart-toggle {{ display: flex; gap: 10px; margin-bottom: 15px; }}
        .chart-toggle button {{ padding: 8px 16px; border-radius: 6px; border: none; cursor: pointer; background: var(--bg-tertiary); color: var(--text-primary); }}
        .chart-toggle button.active {{ background: var(--accent); color: white; }}
        .footer {{ text-align: center; padding: 30px; color: var(--text-secondary); font-size: 0.85em; }}
        .footer a {{ color: var(--accent); }}
        .anexo-card {{ background: var(--bg-secondary); padding: 15px; border-radius: 8px; margin-bottom: 10px; }}
        .anexo-card .anexo-title {{ font-weight: bold; margin-bottom: 5px; }}
        .anexo-card .anexo-resumen {{ color: var(--text-secondary); font-size: 0.9em; }}
    </style>
</head>
<body>
    <button class="toggle-btn" onclick="toggleSidebar()">☰</button>
    <div class="container">
        <aside class="sidebar" id="sidebar">
            <h2>🔍 Monitor de Gastos</h2>
            <div class="sidebar-section">
                <h3>📅 Fecha</h3>
                <ul class="date-list" id="dateList">
'''
    
    for i, date in enumerate(dates):
        d = all_data[date]
        active = "active" if i == 0 else ""
        html += f'                    <li class="{active}" onclick="loadDate(\'{date}\')">{d["fecha_display"]} (N° {d["numero_boletin"]})</li>\n'
    
    html += f'''                </ul>
            </div>
            <div class="sidebar-section">
                <h3>📋 Vista</h3>
                <ul class="tab-list">
                    <li class="active" onclick="showTab('gastos')">💰 Gastos</li>
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
                <h1>Boletín Oficial N° <span id="numBoletin">{latest['numero_boletin']}</span></h1>
                <p>Fecha: <span id="fechaDisplay">{latest['fecha_display']}</span></p>
                <div class="header-controls">
                    <select class="filter-select" id="filterOrganismo" onchange="filterByOrganismo()">
                        <option value="">🏛️ Todos los organismos</option>
'''
    
    for org in all_organismos:
        html += f'                        <option value="{org}">{org[:50]}</option>\n'
    
    html += f'''                    </select>
                    <div class="stats">
                        <div class="stat"><div class="stat-value" id="statTotal">{latest['total_normas']}</div><div class="stat-label">Normas</div></div>
                        <div class="stat"><div class="stat-value" id="statGastos">{len(latest['gastos'])}</div><div class="stat-label">Gastos</div></div>
                        <div class="stat"><div class="stat-value" id="statAnexos">{latest.get('total_anexos', 0)}</div><div class="stat-label">Anexos</div></div>
'''
    if pending_count:
        html += f'                        <div class="stat warning"><div class="stat-value">{pending_count}</div><div class="stat-label">Pendientes</div></div>\n'
    
    html += '''                    </div>
                </div>
            </div>
            
            <div class="tab-content active" id="tab-gastos">
                <div class="card-grid" id="gastosGrid">
'''
    
    # Generate gasto cards with expandable details
    for idx, g in enumerate(latest['gastos']):
        expensive = "expensive" if g.get('monto', 0) > 100_000_000 else ""
        desc_corto = g.get('resumen_corto', g.get('sumario', ''))
        desc_largo = g.get('resumen_largo', '')
        org = g.get('organismo', '')
        
        anexos_html = ""
        if g.get('anexos'):
            anexos_html = '<div class="anexos-list">📎 '
            for anx in g['anexos']:
                anexo_id = anx['nombre'].replace('.', '_').replace('-', '_')
                anexos_html += f'<span class="anexo-link" onclick="goToAnexo(\'{anexo_id}\')">{anx["nombre"][:25]}</span>'
            anexos_html += '</div>'
        
        html += f'''                    <div class="card {expensive}" data-organismo="{org}">
                        <div class="amount">{g.get('monto_fmt', '$0')}</div>
                        <div class="desc">{desc_corto}</div>
                        <div class="desc-long">{desc_largo}</div>
                        <div class="meta">
                            <span class="tag">{org[:35]}</span>
                            <button class="btn secondary" onclick="this.closest('.card').classList.toggle('expanded')">Ver más</button>
                            <a href="{g.get('url', '#')}" target="_blank" class="btn">Ver PDF</a>
                        </div>
                        {anexos_html}
                    </div>
'''
    
    html += '''                </div>
            </div>
            
            <div class="tab-content" id="tab-otros">
                <div class="card-grid" id="otrosGrid">
'''
    
    for s in latest.get('sin_gastos', []):
        desc = s.get('resumen_corto', s.get('sumario', ''))
        org = s.get('organismo', '')
        html += f'''                    <div class="card" data-organismo="{org}">
                        <div class="desc"><strong>{s.get('nombre', '')}</strong></div>
                        <div class="desc">{desc}</div>
                        <div class="meta">
                            <span class="tag">{org[:35]}</span>
                            <a href="{s.get('url', '#')}" target="_blank" class="btn">Ver PDF</a>
                        </div>
                    </div>
'''
    
    html += '''                </div>
            </div>
            
            <div class="tab-content" id="tab-anexos">
                <div class="card-grid" id="anexosGrid">
'''
    
    # Generate anexo cards with summaries
    for norm in latest['gastos'] + latest.get('sin_gastos', []):
        for anx in norm.get('anexos', []):
            anexo_id = anx['nombre'].replace('.', '_').replace('-', '_')
            resumen = anx.get('resumen', 'Sin resumen disponible')
            html += f'''                    <div class="anexo-card" id="anexo_{anexo_id}">
                        <div class="anexo-title">📄 {anx['nombre']}</div>
                        <div class="desc"><strong>Norma:</strong> {norm.get('nombre', '')}</div>
                        <div class="anexo-resumen">{resumen}</div>
                        <div class="meta" style="margin-top:10px;">
                            <a href="{anx.get('url', '#')}" target="_blank" class="btn">Descargar PDF</a>
                        </div>
                    </div>
'''
    
    html += '''                </div>
            </div>
            
            <div class="tab-content" id="tab-stats">
                <div class="chart-container">
                    <div class="chart-toggle">
                        <button class="active" onclick="updateChart('count')">📊 Cantidad de gastos</button>
                        <button onclick="updateChart('amount')">💰 Monto total</button>
                    </div>
                    <canvas id="statsChart" height="400"></canvas>
                </div>
            </div>
            
            <div class="footer">
                Generado automáticamente con IA por <a href="https://github.com/ignaciokairuz/Boletin_Oficial_AI">Boletin_Oficial_AI</a>
            </div>
        </main>
    </div>
    
    <script>
        const allData = ''' + json.dumps(all_data, ensure_ascii=False) + ''';
        let currentChart = null;
        let chartMode = 'count';
        
        // Theme
        function toggleTheme() {
            document.body.classList.toggle('light-mode');
            localStorage.setItem('theme', document.body.classList.contains('light-mode') ? 'light' : 'dark');
            if (currentChart) updateChart(chartMode);
        }
        if (localStorage.getItem('theme') === 'light') document.body.classList.add('light-mode');
        
        function toggleSidebar() {
            document.getElementById('sidebar').classList.toggle('collapsed');
            document.getElementById('main').classList.toggle('expanded');
            document.querySelector('.toggle-btn').classList.toggle('collapsed');
        }
        
        function showTab(tab) {
            document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-list li').forEach(t => t.classList.remove('active'));
            document.getElementById('tab-' + tab).classList.add('active');
            event.target.classList.add('active');
            if (tab === 'stats') initChart();
        }
        
        function goToAnexo(anexoId) {
            showTab('anexos');
            document.querySelectorAll('.tab-list li').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-list li')[2].classList.add('active');
            setTimeout(() => {
                const el = document.getElementById('anexo_' + anexoId);
                if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }, 100);
        }
        
        function filterByOrganismo() {
            const filter = document.getElementById('filterOrganismo').value.toLowerCase();
            document.querySelectorAll('.card').forEach(card => {
                const org = (card.dataset.organismo || '').toLowerCase();
                card.style.display = (!filter || org.includes(filter)) ? 'block' : 'none';
            });
        }
        
        function loadDate(date) {
            const data = allData[date];
            if (!data) return;
            document.getElementById('numBoletin').textContent = data.numero_boletin;
            document.getElementById('fechaDisplay').textContent = data.fecha_display;
            document.getElementById('statTotal').textContent = data.total_normas;
            document.getElementById('statGastos').textContent = data.gastos.length;
            document.getElementById('statAnexos').textContent = data.total_anexos || 0;
            document.querySelectorAll('.date-list li').forEach(li => li.classList.remove('active'));
            event.target.classList.add('active');
            // Rebuild grids...
            const gastosGrid = document.getElementById('gastosGrid');
            gastosGrid.innerHTML = data.gastos.map(g => {
                const expensive = g.monto > 100000000 ? 'expensive' : '';
                const anexosHtml = g.anexos && g.anexos.length ? '<div class="anexos-list">📎 ' + g.anexos.map(a => `<span class="anexo-link" onclick="goToAnexo('${a.nombre.replace(/[.-]/g,'_')}')">${a.nombre.substring(0,25)}</span>`).join('') + '</div>' : '';
                return `<div class="card ${expensive}" data-organismo="${g.organismo || ''}">
                    <div class="amount">${g.monto_fmt || '$0'}</div>
                    <div class="desc">${g.resumen_corto || g.sumario || ''}</div>
                    <div class="desc-long">${g.resumen_largo || ''}</div>
                    <div class="meta">
                        <span class="tag">${(g.organismo || '').substring(0,35)}</span>
                        <button class="btn secondary" onclick="this.closest('.card').classList.toggle('expanded')">Ver más</button>
                        <a href="${g.url || '#'}" target="_blank" class="btn">Ver PDF</a>
                    </div>
                    ${anexosHtml}
                </div>`;
            }).join('');
            if (currentChart) initChart();
        }
        
        function initChart() {
            const ctx = document.getElementById('statsChart').getContext('2d');
            const dates = Object.keys(allData).sort();
            const orgs = new Set();
            dates.forEach(d => allData[d].gastos.forEach(g => orgs.add(g.organismo || 'Otros')));
            const orgList = Array.from(orgs).slice(0, 10); // Top 10
            
            const colors = ['#e94560','#4ecca3','#ffc107','#00bcd4','#9c27b0','#ff5722','#2196f3','#8bc34a','#795548','#607d8b'];
            
            const datasets = orgList.map((org, i) => ({
                label: org.substring(0, 25),
                data: dates.map(d => {
                    const gastos = allData[d].gastos.filter(g => g.organismo === org);
                    return chartMode === 'count' ? gastos.length : gastos.reduce((sum, g) => sum + (g.monto || 0), 0);
                }),
                backgroundColor: colors[i % colors.length]
            }));
            
            if (currentChart) currentChart.destroy();
            currentChart = new Chart(ctx, {
                type: 'bar',
                data: { labels: dates.map(d => allData[d].fecha_display), datasets },
                options: {
                    responsive: true,
                    scales: {
                        x: { stacked: true, ticks: { color: getComputedStyle(document.body).getPropertyValue('--text-primary') } },
                        y: { stacked: true, ticks: { color: getComputedStyle(document.body).getPropertyValue('--text-primary') } }
                    },
                    plugins: { legend: { labels: { color: getComputedStyle(document.body).getPropertyValue('--text-primary') } } }
                }
            });
        }
        
        function updateChart(mode) {
            chartMode = mode;
            document.querySelectorAll('.chart-toggle button').forEach(b => b.classList.remove('active'));
            event.target.classList.add('active');
            initChart();
        }
    </script>
</body>
</html>'''
    
    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(html)
    
    print(f"✅ index.html generado con {len(dates)} fecha(s)")

if __name__ == "__main__":
    main()
