"""
Boletín Oficial - Análisis COMPLETO de Gastos v2
-------------------------------------------------
Versión mejorada que:
1. Analiza TODAS las normas (no filtra por keywords)
2. Busca "$" en el texto del PDF
3. Guarda datos por fecha (sin sobreescribir)
4. Incluye normas SIN gastos (resumidas)
5. Genera HTML con sidebar y pestañas
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
    """Extrae TODOS los montos encontrados en el texto"""
    if not text: return []
    matches = re.finditer(AMOUNT_REGEX, text)
    amounts = []
    for m in matches:
        val_str = m.group(1).replace('.', '').replace(',', '.')
        try:
            val = float(val_str)
            if val > 0:
                amounts.append(val)
        except:
            continue
    return amounts

def get_ai_summary(client, item, system_prompt):
    """Genera resumen con IA para una norma"""
    prompt = f"""Norma: {item.get('nombre', '')}
Organismo: {item.get('organismo', '')}
Sumario: {item.get('sumario', '')}
Texto: {item.get('text_snippet', '')[:400]}"""
    
    try:
        result = client.predict(
            message=prompt,
            system_prompt=system_prompt,
            temperature=0.3,
            api_name="/chat"
        )
        resp = result.split("**💬 Response:**")[1].strip() if "**💬 Response:**" in result else result
        return resp.strip()
    except:
        return item.get('sumario', '')

def main():
    # Crear directorio de datos si no existe
    os.makedirs(DATA_DIR, exist_ok=True)
    
    print("📥 Descargando boletín...")
    response = requests.get(API_URL)
    data = response.json()
    
    boletin = data.get('boletin', {})
    fecha_raw = boletin.get('fecha_publicacion', '?')
    numero = boletin.get('numero', '?')
    
    # Convertir fecha a formato archivo
    try:
        fecha_parts = fecha_raw.split('/')
        fecha_iso = f"{fecha_parts[2]}-{fecha_parts[1].zfill(2)}-{fecha_parts[0].zfill(2)}"
    except:
        fecha_iso = datetime.now().strftime('%Y-%m-%d')
    
    data_file = os.path.join(DATA_DIR, f"{fecha_iso}.json")
    
    # Verificar si ya existe (backup redundante)
    if os.path.exists(data_file):
        print(f"⚠️ Ya existe datos para {fecha_iso}, saltando...")
        # Igual regeneramos el HTML por si hay cambios
        regenerate_html()
        return
    
    print(f"📋 Boletín N° {numero} - Fecha: {fecha_raw} ({fecha_iso})")
    
    # Extraer TODAS las normas del índice
    all_norms = []
    normas_root = data.get('normas', {}).get('normas', {})
    
    for poder, tipos in normas_root.items():
        for tipo, organismos in tipos.items():
            for organismo, lista in organismos.items():
                for item in lista:
                    all_norms.append({
                        'nombre': item.get('nombre'),
                        'sumario': item.get('sumario'),
                        'url': item.get('url_norma'),
                        'tipo': tipo,
                        'organismo': organismo
                    })
    
    print(f"📊 Total normas en el índice: {len(all_norms)}")
    
    # Analizar CADA norma
    print(f"\n🔍 Analizando TODAS las normas...\n")
    
    gastos = []
    sin_gastos = []
    errores = 0
    
    for i, item in enumerate(all_norms):
        print(f"[{i+1}/{len(all_norms)}] {item['nombre'][:50]}...", end="", flush=True)
        
        try:
            r = requests.get(item['url'], timeout=120)
            if r.status_code != 200:
                print(" ❌ HTTP error")
                errores += 1
                continue
                
            with io.BytesIO(r.content) as f:
                reader = PdfReader(f)
                text = ""
                for page in reader.pages:
                    text += page.extract_text() + "\n"
            
            amounts = extract_amounts(text)
            item['text_snippet'] = text[:600]
            
            if amounts:
                max_amt = max(amounts)
                item['monto'] = max_amt
                item['monto_fmt'] = f"${max_amt:,.2f}"
                item['todos_montos'] = len(amounts)
                gastos.append(item)
                print(f" 💰 {item['monto_fmt']}")
            else:
                sin_gastos.append(item)
                print(" (sin $)")
                
        except Exception as e:
            print(f" ❌ Error: {str(e)[:30]}")
            errores += 1
        
        time.sleep(0.3)
    
    # Ordenar gastos por monto
    gastos.sort(key=lambda x: x['monto'], reverse=True)
    
    # === Generar resúmenes con IA ===
    print(f"\n🤖 Generando resúmenes con IA...")
    
    try:
        client = Client("amd/gpt-oss-120b-chatbot")
        
        GASTOS_PROMPT = """Eres un analista de gastos públicos argentinos. 
Dado un gasto del gobierno, explica en UNA sola oración de 15-25 palabras cuál es el fin/propósito del gasto.
Responde SOLO con la oración, sin formato adicional."""
        
        OTROS_PROMPT = """Eres un analista de normativas públicas argentinas.
Dada una norma del gobierno, explica en UNA sola oración de 15-20 palabras de qué trata.
Responde SOLO con la oración, sin formato adicional."""
        
        # Top 30 gastos
        for i, g in enumerate(gastos[:30]):
            print(f"  [Gasto {i+1}/30] {g['nombre'][:35]}...", end=" ", flush=True)
            g['resumen_ia'] = get_ai_summary(client, g, GASTOS_PROMPT)
            print("✓")
        
        # Top 20 sin gastos
        for i, s in enumerate(sin_gastos[:20]):
            print(f"  [Otro {i+1}/20] {s['nombre'][:35]}...", end=" ", flush=True)
            s['resumen_ia'] = get_ai_summary(client, s, OTROS_PROMPT)
            print("✓")
            
    except Exception as e:
        print(f"⚠️ Error IA: {e}")
        for g in gastos:
            g['resumen_ia'] = g.get('sumario', '')
        for s in sin_gastos:
            s['resumen_ia'] = s.get('sumario', '')
    
    # Guardar datos del día
    day_data = {
        'fecha': fecha_iso,
        'fecha_display': fecha_raw,
        'numero_boletin': numero,
        'total_normas': len(all_norms),
        'gastos': gastos,
        'sin_gastos': sin_gastos[:50],  # Limitar a 50
        'errores': errores
    }
    
    with open(data_file, 'w', encoding='utf-8') as f:
        json.dump(day_data, f, indent=2, ensure_ascii=False)
    
    print(f"\n✅ Guardado: {data_file}")
    
    # Regenerar HTML
    regenerate_html()
    
    print(f"\n" + "="*60)
    print(f"📊 RESUMEN - Boletín N° {numero} ({fecha_raw})")
    print(f"="*60)
    print(f"  Total normas: {len(all_norms)}")
    print(f"  Con gastos:   {len(gastos)}")
    print(f"  Sin gastos:   {len(sin_gastos)}")
    print(f"  Errores:      {errores}")

def regenerate_html():
    """Regenera el index.html con todos los datos disponibles"""
    print("🌍 Regenerando index.html...")
    
    # Listar todas las fechas disponibles
    if not os.path.exists(DATA_DIR):
        print("⚠️ No hay datos aún")
        return
    
    dates = sorted([f.replace('.json', '') for f in os.listdir(DATA_DIR) if f.endswith('.json')], reverse=True)
    
    if not dates:
        print("⚠️ No hay datos aún")
        return
    
    # Cargar todos los datos
    all_data = {}
    for date in dates:
        with open(os.path.join(DATA_DIR, f"{date}.json"), 'r', encoding='utf-8') as f:
            all_data[date] = json.load(f)
    
    latest = all_data[dates[0]]
    
    # Generar HTML con sidebar
    html = f'''<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Monitor de Gastos Públicos</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #1a1a2e; color: #eee; }}
        
        /* Layout */
        .container {{ display: flex; min-height: 100vh; }}
        
        /* Sidebar */
        .sidebar {{ width: 280px; background: #16213e; padding: 20px; border-right: 1px solid #0f3460; position: fixed; height: 100vh; overflow-y: auto; transition: transform 0.3s; }}
        .sidebar.collapsed {{ transform: translateX(-280px); }}
        .sidebar h2 {{ color: #e94560; margin-bottom: 20px; font-size: 1.2em; }}
        .sidebar-section {{ margin-bottom: 25px; }}
        .sidebar-section h3 {{ color: #aaa; font-size: 0.8em; text-transform: uppercase; margin-bottom: 10px; letter-spacing: 1px; }}
        .date-list {{ list-style: none; }}
        .date-list li {{ padding: 8px 12px; cursor: pointer; border-radius: 6px; margin-bottom: 4px; transition: background 0.2s; }}
        .date-list li:hover {{ background: #0f3460; }}
        .date-list li.active {{ background: #e94560; color: white; }}
        .tab-list {{ list-style: none; }}
        .tab-list li {{ padding: 10px 12px; cursor: pointer; border-radius: 6px; margin-bottom: 4px; display: flex; align-items: center; gap: 8px; }}
        .tab-list li:hover {{ background: #0f3460; }}
        .tab-list li.active {{ background: #0f3460; border-left: 3px solid #e94560; }}
        
        /* Toggle button */
        .toggle-btn {{ position: fixed; left: 280px; top: 20px; background: #e94560; border: none; color: white; padding: 10px; border-radius: 0 6px 6px 0; cursor: pointer; z-index: 100; transition: left 0.3s; }}
        .toggle-btn.collapsed {{ left: 0; }}
        
        /* Main content */
        .main {{ margin-left: 280px; flex: 1; padding: 30px; transition: margin-left 0.3s; }}
        .main.expanded {{ margin-left: 0; }}
        
        /* Header */
        .header {{ background: linear-gradient(135deg, #0f3460 0%, #16213e 100%); padding: 25px; border-radius: 12px; margin-bottom: 25px; }}
        .header h1 {{ font-size: 1.8em; margin-bottom: 8px; }}
        .header .stats {{ display: flex; gap: 20px; margin-top: 15px; flex-wrap: wrap; }}
        .stat {{ background: rgba(233,69,96,0.2); padding: 10px 15px; border-radius: 8px; }}
        .stat-value {{ font-size: 1.4em; font-weight: bold; color: #e94560; }}
        .stat-label {{ font-size: 0.8em; color: #aaa; }}
        
        /* Cards */
        .card-grid {{ display: grid; gap: 15px; }}
        .card {{ background: #16213e; padding: 20px; border-radius: 10px; border-left: 4px solid #0f3460; transition: transform 0.2s, box-shadow 0.2s; }}
        .card:hover {{ transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.3); }}
        .card.expensive {{ border-left-color: #e94560; }}
        .card .amount {{ font-size: 1.3em; font-weight: bold; color: #4ecca3; margin-bottom: 8px; }}
        .card.expensive .amount {{ color: #e94560; }}
        .card .desc {{ color: #ccc; line-height: 1.5; margin-bottom: 10px; }}
        .card .meta {{ display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }}
        .tag {{ background: #0f3460; padding: 4px 10px; border-radius: 4px; font-size: 0.8em; }}
        .btn {{ background: #e94560; color: white; padding: 6px 12px; border-radius: 4px; text-decoration: none; font-size: 0.85em; }}
        .btn:hover {{ background: #ff6b6b; }}
        
        /* Tab content */
        .tab-content {{ display: none; }}
        .tab-content.active {{ display: block; }}
        
        /* Footer */
        .footer {{ text-align: center; padding: 30px; color: #666; font-size: 0.85em; }}
        .footer a {{ color: #e94560; }}
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
    
    # Agregar lista de fechas
    for i, date in enumerate(dates):
        d = all_data[date]
        active = "active" if i == 0 else ""
        html += f'                    <li class="{active}" onclick="loadDate(\'{date}\')">{d["fecha_display"]} (N° {d["numero_boletin"]})</li>\n'
    
    html += '''                </ul>
            </div>
            
            <div class="sidebar-section">
                <h3>📋 Vista</h3>
                <ul class="tab-list">
                    <li class="active" onclick="showTab('gastos')">💰 Gastos</li>
                    <li onclick="showTab('otros')">📄 Otras Normas</li>
                </ul>
            </div>
        </aside>
        
        <main class="main" id="main">
            <div class="header" id="header">
                <h1>Boletín Oficial N° <span id="numBoletin">''' + str(latest['numero_boletin']) + '''</span></h1>
                <p>Fecha: <span id="fechaDisplay">''' + latest['fecha_display'] + '''</span></p>
                <div class="stats">
                    <div class="stat">
                        <div class="stat-value" id="statTotal">''' + str(latest['total_normas']) + '''</div>
                        <div class="stat-label">Normas analizadas</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value" id="statGastos">''' + str(len(latest['gastos'])) + '''</div>
                        <div class="stat-label">Con gastos</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value" id="statOtros">''' + str(len(latest['sin_gastos'])) + '''</div>
                        <div class="stat-label">Otras normas</div>
                    </div>
                </div>
            </div>
            
            <div class="tab-content active" id="tab-gastos">
                <div class="card-grid" id="gastosGrid">
'''
    
    # Agregar cards de gastos
    for g in latest['gastos']:
        expensive = "expensive" if g.get('monto', 0) > 100_000_000 else ""
        desc = g.get('resumen_ia', g.get('sumario', ''))
        org = g.get('organismo', '')[:30]
        html += f'''                    <div class="card {expensive}">
                        <div class="amount">{g.get('monto_fmt', '$0')}</div>
                        <div class="desc">{desc}</div>
                        <div class="meta">
                            <span class="tag">{org}</span>
                            <a href="{g.get('url', '#')}" target="_blank" class="btn">Ver PDF</a>
                        </div>
                    </div>
'''
    
    html += '''                </div>
            </div>
            
            <div class="tab-content" id="tab-otros">
                <div class="card-grid" id="otrosGrid">
'''
    
    # Agregar cards de otras normas
    for s in latest.get('sin_gastos', []):
        desc = s.get('resumen_ia', s.get('sumario', ''))
        org = s.get('organismo', '')[:30]
        html += f'''                    <div class="card">
                        <div class="desc"><strong>{s.get('nombre', '')}</strong></div>
                        <div class="desc">{desc}</div>
                        <div class="meta">
                            <span class="tag">{org}</span>
                            <a href="{s.get('url', '#')}" target="_blank" class="btn">Ver PDF</a>
                        </div>
                    </div>
'''
    
    html += '''                </div>
            </div>
            
            <div class="footer">
                Generado automáticamente con IA por <a href="https://github.com/ignaciokairuz/Boletin_Oficial_AI">Boletin_Oficial_AI</a>
            </div>
        </main>
    </div>
    
    <script>
        // Data store
        const allData = ''' + json.dumps(all_data, ensure_ascii=False) + ''';
        
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
        }
        
        function loadDate(date) {
            const data = allData[date];
            if (!data) return;
            
            // Update header
            document.getElementById('numBoletin').textContent = data.numero_boletin;
            document.getElementById('fechaDisplay').textContent = data.fecha_display;
            document.getElementById('statTotal').textContent = data.total_normas;
            document.getElementById('statGastos').textContent = data.gastos.length;
            document.getElementById('statOtros').textContent = data.sin_gastos.length;
            
            // Update date list
            document.querySelectorAll('.date-list li').forEach(li => li.classList.remove('active'));
            event.target.classList.add('active');
            
            // Update gastos grid
            const gastosGrid = document.getElementById('gastosGrid');
            gastosGrid.innerHTML = data.gastos.map(g => `
                <div class="card ${g.monto > 100000000 ? 'expensive' : ''}">
                    <div class="amount">${g.monto_fmt || '$0'}</div>
                    <div class="desc">${g.resumen_ia || g.sumario || ''}</div>
                    <div class="meta">
                        <span class="tag">${(g.organismo || '').substring(0, 30)}</span>
                        <a href="${g.url || '#'}" target="_blank" class="btn">Ver PDF</a>
                    </div>
                </div>
            `).join('');
            
            // Update otros grid
            const otrosGrid = document.getElementById('otrosGrid');
            otrosGrid.innerHTML = (data.sin_gastos || []).map(s => `
                <div class="card">
                    <div class="desc"><strong>${s.nombre || ''}</strong></div>
                    <div class="desc">${s.resumen_ia || s.sumario || ''}</div>
                    <div class="meta">
                        <span class="tag">${(s.organismo || '').substring(0, 30)}</span>
                        <a href="${s.url || '#'}" target="_blank" class="btn">Ver PDF</a>
                    </div>
                </div>
            `).join('');
        }
    </script>
</body>
</html>'''
    
    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(html)
    
    print(f"✅ index.html generado con {len(dates)} fecha(s)")

if __name__ == "__main__":
    main()
