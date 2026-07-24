# Agente de impresión térmica — instalación y operación (ADR 0033)

Servicio local por sede: se conecta SALIENTE al backend, recibe los trabajos de la cola de
impresión (`/api/v1/impresion`) y los imprime en ESC/POS. Imprime aunque no haya ningún navegador
abierto (la comanda de un pedido de WhatsApp sale sola en cocina).

## 1. Emitir el token del dispositivo

Como **admin**, en la API del tenant (dashboard en R4):

```
POST /api/v1/impresion/dispositivos   {"nombre": "Caja principal"}
→ {"id": 1, "nombre": "Caja principal", "token": "imp_..."}   ← se muestra UNA sola vez
```

Revocar: `POST /api/v1/impresion/dispositivos/{id}/revocar`. El token solo autoriza la
superficie de impresión — jamás datos del negocio.

## 2. Configurar

Copiar `tools/agente_impresion/config.example.json` como `config.json` junto al ejecutable y
editar: URL del tenant, slug, token y el **mapeo zona→impresora** (ADR 0033 D4 — vive aquí, en
la sede, no en el servidor):

- `"tipo": "red"` — térmica de red en el puerto 9100 (el estándar de restaurante). `destino` = IP.
- `"tipo": "windows"` — impresora instalada en Windows (driver genérico/texto). `destino` = nombre
  exacto de la impresora en Windows. Requiere `pywin32` (incluido en el binario).
- `"tipo": "archivo"` — debug: escribe el ESC/POS crudo a un archivo.
- La zona `"*"` es el default: recibe comandas de zonas sin mapear, precuentas y comprobantes.

## 3. Correr

```bash
# Desde el repo (dev):
uv sync --extra impresion
.venv/Scripts/python.exe -m tools.agente_impresion ruta/al/config.json

# Binario Windows (sede): doble clic a agente_impresion.exe (lee config.json de su carpeta)
```

Artefactos junto al config: `agente_impresion.log` (log local) e `impresos.txt` (registro de
trabajos ya impresos — el guardarraíl local contra el papel doble tras un corte de conexión;
no borrarlo mientras el agente opere).

## 4. Construir el .exe (Windows primero)

```bash
uv run --with pyinstaller pyinstaller --onefile --name agente_impresion \
    --collect-submodules escpos --collect-data escpos --copy-metadata python-escpos \
    tools/agente_impresion/pyinstaller_entry.py
# --collect-data es OBLIGATORIO: sin él falta escpos/capabilities.json y toda impresión falla
# → dist/agente_impresion.exe  (autocontenido; llevar junto a config.json)
```

`pyinstaller_entry.py` existe porque PyInstaller no empaqueta `__main__.py` de un paquete
directamente. El binario incluye httpx + python-escpos (+ pywin32 en Windows).

## 5. Anti-papel-doble (cómo funciona, para troubleshooting)

1. El backend entrega un trabajo y lo marca `entregado_agente`; sin ack en 120s lo **re-entrega**.
2. El agente imprime, escribe el id en `impresos.txt` y LUEGO ackea.
3. Si la conexión se corta entre imprimir y ackear, la re-entrega llega a un agente que ya tiene
   el id en su registro: **no imprime de nuevo**, solo confirma.
4. La clave UNIQUE (`idempotency_key`) en el backend impide duplicar trabajos desde el origen.

## 6. Impresoras genéricas (perfil conservador)

El render usa solo comandos ESC/POS universales (texto, negrita, tamaños, corte). Si una térmica
china no corta o imprime caracteres raros en tildes, probar primero `"tipo": "red"` contra su IP;
el soporte de perfiles específicos por modelo queda para cuando haya un caso real.
