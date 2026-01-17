"""
Boletín Oficial - Análisis COMPLETO de Gastos
----------------------------------------------
Versión mejorada que:
1. Analiza TODAS las normas (no filtra por keywords)
2. Busca "$" en el texto del PDF
3. Solo considera gastos cuando encuentra montos
4. Usa IA para generar resumen del fin del gasto
"""
import requests
import json
import io
import re
import time
from pypdf import PdfReader
from gradio_client import Client

API_URL = "https://api-restboletinoficial.buenosaires.gob.ar/obtenerBoletin/0/true"
AMOUNT_REGEX = r'\$\s?(\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?)'

def extract_amounts(text):
    """Extrae TODOS los montos encontrados en el texto"""
    if not text: return []
    matches = re.finditer(AMOUNT_REGEX, text)
    amounts = []
    for m in matches:
        val_str = m.group(1).replace('.', '').replace(',', '.')
        try:
            val = float(val_str)
            if val > 0:  # Ignorar $0
                amounts.append(val)
        except:
            continue
    return amounts

def main():
    print("📥 Descargando boletín...")
    response = requests.get(API_URL)
    data = response.json()
    
    boletin = data.get('boletin', {})
    fecha = boletin.get('fecha_publicacion', '?')
    numero = boletin.get('numero', '?')
    print(f"📋 Boletín N° {numero} - Fecha: {fecha}")
    
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
    
    # Analizar CADA norma buscando "$"
    print(f"\n🔍 Analizando TODAS las normas (puede tomar varios minutos)...\n")
    
    gastos = []
    sin_monto = 0
    errores = 0
    
    for i, item in enumerate(all_norms):
        print(f"[{i+1}/{len(all_norms)}] {item['nombre'][:50]}...", end="", flush=True)
        
        try:
            r = requests.get(item['url'], timeout=15)
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
            
            if amounts:
                max_amt = max(amounts)
                item['monto'] = max_amt
                item['monto_fmt'] = f"${max_amt:,.2f}"
                item['text_snippet'] = text[:600]
                item['todos_montos'] = len(amounts)
                gastos.append(item)
                print(f" 💰 {item['monto_fmt']} ({len(amounts)} montos encontrados)")
            else:
                sin_monto += 1
                print(" (sin $)")
                
        except Exception as e:
            print(f" ❌ Error: {str(e)[:30]}")
            errores += 1
        
        time.sleep(0.3)  # Ser amable con el servidor
    
    # Ordenar por monto
    gastos.sort(key=lambda x: x['monto'], reverse=True)
    
    # === PASO 2: Generar resumen con IA ===
    print(f"\n🤖 Generando resumen de gastos con IA (top {min(30, len(gastos))})...\n")
    
    try:
        client = Client("amd/gpt-oss-120b-chatbot")
        
        SYSTEM_PROMPT = """Eres un analista de gastos públicos argentinos. 
Dado un gasto del gobierno, explica en UNA sola oración de 15-25 palabras cuál es el fin/propósito del gasto.
Responde SOLO con la oración, sin formato adicional."""
        
        for i, g in enumerate(gastos[:30]):  # Top 30
            print(f"  [{i+1}/30] {g['nombre'][:35]}...", end=" ", flush=True)
            
            prompt = f"""Norma: {g['nombre']}
Monto: {g['monto_fmt']}
Organismo: {g['organismo']}
Sumario: {g['sumario']}
Texto: {g.get('text_snippet', '')[:400]}"""
            
            try:
                result = client.predict(
                    message=prompt,
                    system_prompt=SYSTEM_PROMPT,
                    temperature=0.3,
                    api_name="/chat"
                )
                # Limpiar respuesta
                resp = result.split("**💬 Response:**")[1].strip() if "**💬 Response:**" in result else result
                g['resumen_ia'] = resp.strip()
                print("✓")
            except Exception as e:
                g['resumen_ia'] = g['sumario']  # Fallback al sumario original
                print(f"(usando sumario)")
                
    except Exception as e:
        print(f"⚠️ Error conectando con IA: {e}")
        print("   Usando sumarios originales como descripciones.")
        for g in gastos:
            g['resumen_ia'] = g.get('sumario', '')
    
    # Guardar resultados
    with open('gastos_completos.json', 'w', encoding='utf-8') as f:
        json.dump(gastos, f, indent=2, ensure_ascii=False)
    
    # Generar reporte markdown
    print(f"\n📝 Generando reporte...")
    
    md = f"# 🔍 Gastos del Boletín Oficial N° {numero} ({fecha})\n\n"
    md += f"**Total normas analizadas:** {len(all_norms)}  \n"
    md += f"**Normas con montos detectados:** {len(gastos)}\n\n"
    md += "| Monto | Descripción | Organismo | PDF |\n"
    md += "|---:|---|---|:---:|\n"
    
    for g in gastos:
        desc = g.get('resumen_ia', g.get('sumario', ''))
        org = g['organismo'][:25] + "..." if len(g['organismo']) > 25 else g['organismo']
        md += f"| {g['monto_fmt']} | {desc} | {org} | [📄]({g['url']}) |\n"
    
    with open('reporte_gastos_final.md', 'w', encoding='utf-8') as f:
        f.write(md)

    # Generar versión HTML para la web
    print("🌍 Generando index.html...")
    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Monitor de Gastos Públicos</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; background: #f5f5f5; }}
        .header {{ background: white; padding: 20px; border-radius: 8px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .card {{ background: white; padding: 15px; margin-bottom: 15px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); border-left: 5px solid #2196F3; }}
        .card.expensive {{ border-left-color: #f44336; }}
        .amount {{ font-size: 1.2em; font-weight: bold; color: #333; }}
        .date {{ color: #666; font-size: 0.9em; }}
        .tag {{ background: #eee; padding: 2px 8px; border-radius: 4px; font-size: 0.8em; }}
        a.btn {{ display: inline-block; background: #2196F3; color: white; padding: 5px 10px; text-decoration: none; border-radius: 4px; margin-top: 5px; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>🔍 Monitor de Gastos Públicos</h1>
        <p><strong>Boletín Oficial N° {numero}</strong> | Fecha: {fecha}</p>
        <p>Analizadas {len(all_norms)} normas | Detectados {len(gastos)} gastos</p>
    </div>

    <div class="feed">
"""
    for g in gastos:
        is_expensive = g['monto'] > 100_000_000  # Marcar rojos > 100M
        css_class = "expensive" if is_expensive else "normal"
        desc = g.get('resumen_ia', g.get('sumario', ''))
        
        html += f"""
        <div class="card {css_class}">
            <div class="amount">{g['monto_fmt']}</div>
            <p>{desc}</p>
            <div class="meta">
                <span class="tag">{g['organismo']}</span>
                <a href="{g['url']}" target="_blank" class="btn">Ver PDF</a>
            </div>
        </div>
"""
    html += """
    </div>
    <div style="text-align: center; margin-top: 30px; color: #888;">
        Generado automáticamente con IA por <a href="https://github.com/ignaciokairuz/Boletin_Oficial_AI">Boletin_Oficial_AI</a>
    </div>
</body>
</html>"""

    with open('index.html', 'w', encoding='utf-8') as f:
        f.write(html)
    
    print(f"\n" + "="*60)
    print(f"📊 RESUMEN - Boletín N° {numero} ({fecha})")
    print(f"="*60)
    print(f"  Total normas en índice:     {len(all_norms)}")
    print(f"  Normas CON montos ($):      {len(gastos)}")
    print(f"  Normas SIN montos:          {sin_monto}")
    print(f"  Errores de descarga:        {errores}")
    print(f"\n  Top 5 gastos más altos:")
    for i, g in enumerate(gastos[:5], 1):
        print(f"    {i}. {g['monto_fmt']} - {g.get('resumen_ia', g['nombre'])[:50]}")
    
    print(f"\n✅ Archivos generados:")
    print(f"   - gastos_completos.json")
    print(f"   - reporte_gastos_final.md")

if __name__ == "__main__":
    main()
