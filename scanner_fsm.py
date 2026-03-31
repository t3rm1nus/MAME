import os
import time
import subprocess

# Rutas
MAME_DIR = r"C:\proyectos\MAME"
DYN_DIR = os.path.join(MAME_DIR, "dinamicos")
MAME_EXE = os.path.join(MAME_DIR, r"EMULADOR\mame.exe")
LUA_SCRIPT = os.path.join(MAME_DIR, r"lua\ram_dumper.lua")

SIGNAL_FILE = os.path.join(DYN_DIR, "do_dump.txt")
OUTPUT_FILE = os.path.join(DYN_DIR, "dump_out.txt")
RAM_START = 0xFF8000

os.makedirs(DYN_DIR, exist_ok=True)

def request_dump():
    """Pide a Lua un dump y lo lee."""
    if os.path.exists(OUTPUT_FILE):
        os.remove(OUTPUT_FILE)
        
    with open(SIGNAL_FILE, "w") as f:
        f.write("dump")
        
    # Esperar a que Lua lo procese
    while not os.path.exists(OUTPUT_FILE):
        time.sleep(0.05)
        
    time.sleep(0.1) # Dar margen de escritura
    
    with open(OUTPUT_FILE, "r") as f:
        lines = f.readlines()
        
    return [int(line.strip(), 16) for line in lines if line.strip()]

def get_stable_dump(state_name):
    """Toma dos dumps separados por 0.5s para descartar animaciones/timers."""
    print(f"\n=> Saca un dump en {state_name}...")
    dump1 = request_dump()
    print("   Esperando 0.5 segundos para filtrar basura...")
    time.sleep(0.5)
    dump2 = request_dump()
    
    # Quedarnos solo con las direcciones que NO cambiaron (valores estables)
    stable = {}
    for i in range(len(dump1)):
        if dump1[i] == dump2[i]:
            stable[RAM_START + i] = dump1[i]
            
    print(f"   [+] {len(stable)} bytes estables encontrados en {state_name}.")
    return stable

def main():
    print("="*60)
    print(" ESCÁNER DEL MOTOR DE ESTADOS - SF2CE")
    print("="*60)
    
    # Limpiar basura anterior
    for f in [SIGNAL_FILE, OUTPUT_FILE]:
        if os.path.exists(f): os.remove(f)

    print("[1] Lanzando MAME en modo ventana...")
    cmd = [MAME_EXE, "sf2ce", "-window", "-nomaximize", "-autoboot_script", LUA_SCRIPT]
    proc = subprocess.Popen(cmd, cwd=os.path.dirname(MAME_EXE))
    time.sleep(3) # Esperar a que arranque
    
    try:
        input("\n>>> Pon créditos y ve a la pantalla de SELECCIÓN DE PERSONAJE. Pulsa ENTER cuando estés ahí...")
        char_select_state = get_stable_dump("SELECCIÓN DE PERSONAJE")
        
        input("\n>>> Elige a Blanka. Entra en un COMBATE. Déjate pegar un poco, pero quédate jugando. Pulsa ENTER en medio de la pelea...")
        combat_state = get_stable_dump("COMBATE")
        
        input("\n>>> Ahora déjate matar. Deja que llegue a la PANTALLA DE CONTINUE (donde sale la cuenta atrás). Pulsa ENTER...")
        continue_state = get_stable_dump("PANTALLA DE CONTINUE")
        
        # --- LA MAGIA: INTERSECCIÓN Y DIFERENCIA ---
        print("\n" + "="*60)
        print("🔍 ANALIZANDO LA MEMORIA...")
        
        candidates = []
        # Buscamos una dirección que exista en los 3 dumps estables
        for addr in combat_state:
            if addr in char_select_state and addr in continue_state:
                val_combat = combat_state[addr]
                val_char = char_select_state[addr]
                val_cont = continue_state[addr]
                
                # REGLA DE ORO: El valor debe ser diferente en cada pantalla
                if len(set([val_combat, val_char, val_cont])) == 3:
                    candidates.append((addr, val_char, val_combat, val_cont))
                    
        print(f"🎯 ¡Encontrados {len(candidates)} candidatos de Game State!")
        print("="*60)
        print(f"{'DIRECCIÓN':<12} | {'CHAR SELECT':<12} | {'COMBATE':<12} | {'CONTINUE':<12}")
        print("-" * 60)
        
        for addr, v_char, v_comb, v_cont in candidates:
            # Filtramos un poco: los game states suelen ser números bajos (0x00, 0x02, 0x04, 0x06...)
            if all(v <= 0x20 for v in [v_char, v_comb, v_cont]):
                print(f"0x{addr:06X}   | 0x{v_char:02X}         | 0x{v_comb:02X}         | 0x{v_cont:02X}    <--- ORO PURO")
            else:
                print(f"0x{addr:06X}   | 0x{v_char:02X}         | 0x{v_comb:02X}         | 0x{v_cont:02X}")
                
    except KeyboardInterrupt:
        pass
    finally:
        print("\nCerrando MAME...")
        proc.kill()

if __name__ == "__main__":
    main()