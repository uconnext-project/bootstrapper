#!/usr/bin/env python3
"""
SWDL Update Authenticator & Manifest Viewer (Windows)
Weryfikuje wszystkie pliki ISO w kontenerze swdl.upd, używając PowerShell do montowania.
Pełna weryfikacja hasha tylko dla installer.iso.
Manifest odczytywany przez pycdlib (preferowane) lub przez skopiowanie i montowanie.
"""

import os
import sys
import argparse
import subprocess
import hashlib
import tempfile
import time
import shutil
from pathlib import Path

# ------------------------------------------------------------
# Konfiguracja (zgodna z oryginalnym loader.lua)
# ------------------------------------------------------------
SPLIT_ISO_NAMES = ["installer.iso", "primary.iso", "secondary.iso"]
MANIFEST_NAME = "etc/manifest.lua"
DEFAULT_PUBKEY = "swdl.pub"

HEADER_SIZE = 32768  # 32 KB
SIGNATURE_SIZE = 256  # pierwsze 256 bajtów nagłówka
BUILDINFO_OFFSET = 256  # offset do danych buildinfo (w nagłówku)
BUILDINFO_SIZE = 256
HASH_OFFSET = 127 * 256  # 32512 – offset podpisanego hasha danych


# ------------------------------------------------------------
# Narzędzia PowerShell do montowania ISO
# ------------------------------------------------------------
def _run_powershell(command, check=True):
    """
    Wykonaj polecenie PowerShell i zwróć stdout.
    Używa listy argumentów (bez shell=True) i kodowania UTF-8.
    """
    full_cmd = ['powershell', '-NoProfile', '-Command', command]
    print(f"[PS] {command}")
    result = subprocess.run(full_cmd, capture_output=True, text=True,
                            encoding='utf-8', errors='replace')
    if check and result.returncode != 0:
        raise RuntimeError(f"Polecenie PowerShell nie powiodło się: {command}\n{result.stderr}")
    return result.stdout.strip()


def mount_iso_powershell(iso_path):
    """
    Montuje plik ISO za pomocą PowerShell Mount-DiskImage.
    Zwraca ścieżkę do zamontowanego katalogu (np. 'D:\\').
    """
    iso_path_str = str(iso_path)

    # Sprawdź, czy obraz już nie jest zamontowany
    check_cmd = f'(Get-DiskImage "{iso_path_str}" -StorageType ISO -ErrorAction SilentlyContinue).Attached'
    attached = _run_powershell(check_cmd)
    if attached == 'True':
        # Obraz już zamontowany – pobierz literę dysku
        drive_letter = _run_powershell(f'(Get-DiskImage "{iso_path_str}" -StorageType ISO | Get-Volume).DriveLetter')
        mount_path = f"{drive_letter}:\\"
        print(f"Obraz już zamontowany jako {mount_path}")
        return mount_path

    # Zamontuj obraz
    _run_powershell(f'Mount-DiskImage -ImagePath "{iso_path_str}" -StorageType ISO')

    # Daj systemowi chwilę na przydzielenie litery dysku
    time.sleep(1)

    # Pobierz literę dysku dla właśnie zamontowanego obrazu
    drive_letter = _run_powershell(f'(Get-DiskImage "{iso_path_str}" -StorageType ISO | Get-Volume).DriveLetter')

    if not drive_letter:
        raise RuntimeError(f"Nie udało się uzyskać litery dysku dla {iso_path_str}")

    mount_path = f"{drive_letter}:\\"
    print(f"Zamontowano {iso_path} jako {mount_path}")
    return mount_path


def dismount_iso_powershell(iso_path):
    """
    Odmontowuje plik ISO za pomocą PowerShell Dismount-DiskImage.
    """
    iso_path_str = str(iso_path)
    try:
        _run_powershell(f'Dismount-DiskImage -ImagePath "{iso_path_str}" -StorageType ISO')
        print(f"Odmontowano {iso_path}")
    except Exception as e:
        print(f"Ostrzeżenie: Nie udało się odmontować {iso_path}: {e}")


# ------------------------------------------------------------
# Narzędzia kryptograficzne
# ------------------------------------------------------------
def run_cmd(cmd, check=True):
    """Wykonaj polecenie w shellu i zwróć stdout."""
    print(f"[CMD] {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                            encoding='utf-8', errors='replace')
    if check and result.returncode != 0:
        raise RuntimeError(f"Polecenie nie powiodło się: {cmd}\n{result.stderr}")
    return result.stdout


def read_chunk(path, offset, size):
    """Odczytaj fragment pliku binarnego."""
    with open(path, 'rb') as f:
        f.seek(offset)
        return f.read(size)


def verify_signature_openssl(pubkey_path, signature_data, data):
    """Weryfikacja podpisu RSA-SHA256 za pomocą openssl."""
    with tempfile.NamedTemporaryFile(delete=False) as sig_f, \
            tempfile.NamedTemporaryFile(delete=False) as data_f:
        sig_f.write(signature_data)
        data_f.write(data)
        sig_path = sig_f.name
        data_path = data_f.name

    try:
        cmd = f'openssl dgst -sha256 -verify "{pubkey_path}" -signature "{sig_path}" "{data_path}"'
        output = run_cmd(cmd, check=False)
        print(output.strip())
        return "Verified OK" in output
    finally:
        os.unlink(sig_path)
        os.unlink(data_path)


def decrypt_hash_openssl(pubkey_path, signed_hash):
    """Odszyfrowanie podpisanego hasha (RSA) za pomocą openssl rsautl."""
    with tempfile.NamedTemporaryFile(delete=False) as sig_f:
        sig_f.write(signed_hash)
        sig_path = sig_f.name

    out_path = sig_path + ".dec"
    try:
        cmd = f'openssl rsautl -verify -inkey "{pubkey_path}" -in "{sig_path}" -pubin -out "{out_path}"'
        run_cmd(cmd)
        with open(out_path, 'rb') as f:
            return f.read()
    finally:
        os.unlink(sig_path)
        if os.path.exists(out_path):
            os.unlink(out_path)


def compute_data_hash(iso_path, skip_bytes=HEADER_SIZE):
    """Oblicz SHA256 danych ISO z pominięciem nagłówka."""
    sha = hashlib.sha256()
    total = os.path.getsize(iso_path)
    processed = skip_bytes
    print("Obliczanie hasha danych ISO...")
    with open(iso_path, 'rb') as f:
        f.seek(skip_bytes)
        while True:
            chunk = f.read(1024 * 1024)  # 1 MB
            if not chunk:
                break
            sha.update(chunk)
            processed += len(chunk)
            percent = (processed / total) * 100 if total else 0
            print(f"\rPostęp: {percent:.1f}%", end='', flush=True)
    print()
    return sha.digest()


def get_buildinfo(data):
    """Parsuj 256-bajtowy blok buildinfo."""
    if len(data) < BUILDINFO_SIZE:
        return {}

    def safe_decode(b, default='?'):
        try:
            s = b.decode('ascii').strip('\x00')
            return s if s else default
        except:
            return b.hex()

    return {
        'isomajver': data[0],
        'isominver': data[1],
        'build_year': safe_decode(data[2:4]),
        'build_week': safe_decode(data[4:6]),
        'build_patch': safe_decode(data[6:8]),
        'model_year': safe_decode(data[8:12]),
        'market': safe_decode(data[12:14]),
        'variant': safe_decode(data[14:17]),
        'isosize': safe_decode(data[17:28], ''),
    }


# ------------------------------------------------------------
# Autentykacja pojedynczego ISO
# ------------------------------------------------------------
def authenticate_iso(iso_path, pubkey_path, check_full_hash=False):
    """
    Autentykacja pliku ISO.
    - Zawsze weryfikuje podpis nagłówka.
    - Jeśli check_full_hash=True, przeprowadza pełną weryfikację hasha danych.
    Zwraca (success, error_message, buildinfo).
    """
    print(f"\n--- Autentykacja: {iso_path} ---")
    if not os.path.exists(iso_path):
        return False, "Plik nie istnieje", {}

    # 1. Odczytaj nagłówek 32KB
    header = read_chunk(iso_path, 0, HEADER_SIZE)
    if len(header) < HEADER_SIZE:
        return False, "Plik ISO za krótki", {}

    # 2. Wyodrębnij podpis (256B) i dane nagłówka (reszta)
    signature = header[:SIGNATURE_SIZE]
    header_data = header[SIGNATURE_SIZE:]

    # 3. Wyodrębnij buildinfo
    buildinfo_data = header[BUILDINFO_OFFSET:BUILDINFO_OFFSET + BUILDINFO_SIZE]
    buildinfo = get_buildinfo(buildinfo_data)
    print("BuildInfo:", buildinfo)

    # 4. Weryfikacja podpisu nagłówka
    if not verify_signature_openssl(pubkey_path, signature, header_data):
        return False, "Nieprawidłowy podpis nagłówka", buildinfo
    print("✔ Podpis nagłówka zweryfikowany.")

    # 5. Opcjonalna pełna weryfikacja hasha danych
    if check_full_hash:
        if os.path.getsize(iso_path) <= HEADER_SIZE + SIGNATURE_SIZE:
            print("Plik ISO nie zawiera sekcji danych – pomijam weryfikację hasha.")
            return True, None, buildinfo

        signed_hash = read_chunk(iso_path, HASH_OFFSET, SIGNATURE_SIZE)
        extracted_hash = decrypt_hash_openssl(pubkey_path, signed_hash)
        computed_hash = compute_data_hash(iso_path, HEADER_SIZE)

        if computed_hash != extracted_hash:
            return False, "Hash danych ISO niezgodny", buildinfo
        print("✔ Hash danych ISO zgodny.")

    return True, None, buildinfo


# ------------------------------------------------------------
# Odczyt manifestu z installer.iso
# ------------------------------------------------------------
def read_manifest_from_iso(iso_path):
    """
    Próbuje odczytać etc/manifest.lua z pliku ISO.
    Najpierw używa pycdlib (jeśli dostępne), w przeciwnym razie kopiuje ISO
    do tymczasowego katalogu i montuje za pomocą PowerShell.
    Zwraca zawartość manifestu jako string lub None w przypadku błędu.
    """
    # Metoda 1: pycdlib (bez montowania)
    try:
        import pycdlib
        iso = pycdlib.PyCdlib()
        iso.open(str(iso_path))
        manifest_path = ("/"+MANIFEST_NAME).replace('\\', '/')
        for dirname, dirlist, filelist in iso.walk(iso_path='/'):
            full = (dirname + '/' if dirname != '/' else '') + manifest_path
            try:
                with tempfile.NamedTemporaryFile(delete=False) as outfp:
                    iso.get_file_from_iso_fp(outfp, iso_path=full)
                    outfp.close()
                    with open(outfp.name, 'r', encoding='utf-8', errors='replace') as f:
                        content = f.read()
                    os.unlink(outfp.name)
                    iso.close()
                    return content
            except Exception as e:
                print(e)
                continue
        iso.close()

    except ImportError as e:
        print(e)
        pass
    except Exception as e:
        print(f"pycdlib error: {e}")

    # Metoda 2: Skopiuj ISO do lokalnego tymczasowego pliku i zamontuj
    print("pycdlib niedostępne – kopiowanie installer.iso do tymczasowego katalogu...")
    tmp_dir = tempfile.mkdtemp()
    tmp_iso = Path(tmp_dir) / "installer.iso"
    try:
        shutil.copy2(str(iso_path), str(tmp_iso))
        mount_point = mount_iso_powershell(tmp_iso)
        manifest_file = Path(mount_point) / MANIFEST_NAME
        if manifest_file.exists():
            with open(manifest_file, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            dismount_iso_powershell(tmp_iso)
            return content
        else:
            dismount_iso_powershell(tmp_iso)
            return None
    except Exception as e:
        print(f"Błąd podczas montowania kopii installer.iso: {e}")
        return None
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ------------------------------------------------------------
# Główna logika
# ------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Weryfikacja aktualizacji Uconnect (SWDL)")
    parser.add_argument("input_file", help="Plik swdl.upd lub pojedynczy ISO")
    parser.add_argument("--pubkey", help="Ścieżka do klucza publicznego PEM")
    parser.add_argument("--no-manifest", action="store_true", help="Nie wyświetlaj manifestu")
    args = parser.parse_args()

    input_path = Path(args.input_file).resolve()
    if not input_path.exists():
        sys.exit(f"Plik {input_path} nie istnieje.")

    # Ustal klucz publiczny
    if args.pubkey:
        pubkey = args.pubkey
    else:
        pubkey = Path.cwd() / DEFAULT_PUBKEY
        if not pubkey.exists():
            sys.exit("Nie podano klucza publicznego i nie znaleziono swdl.pub w bieżącym katalogu.")
    print(f"Klucz publiczny: {pubkey}")

    # Lista do śledzenia zamontowanych obrazów (do sprzątania)
    mounted_images = []

    try:
        # Rozpoznanie typu pliku
        if input_path.name.lower() == "swdl.upd":
            print("Wykryto plik SWDL – montowanie...")
            swdl_mount = mount_iso_powershell(input_path)
            mounted_images.append(input_path)

            # Znajdź wszystkie ISO z listy SPLIT_ISO_NAMES wewnątrz zamontowanego katalogu
            iso_files = {}
            for name in SPLIT_ISO_NAMES:
                candidate = Path(swdl_mount) / name
                if candidate.exists():
                    iso_files[name] = candidate
                    print(f"Znaleziono: {name} -> {candidate}")
                else:
                    print(f"Brak pliku: {name} (opcjonalny)")
        else:
            # Pojedynczy plik ISO – traktujemy jako installer
            iso_files = {"installer.iso": input_path}

        if not iso_files:
            sys.exit("Nie znaleziono żadnego pliku ISO do weryfikacji.")

        # Weryfikuj każdy znaleziony plik ISO
        all_ok = True
        for iso_name, iso_path in iso_files.items():
            # Pełną weryfikację hasha wykonujemy tylko dla installer.iso
            check_full = (iso_name == "installer.iso")
            success, err, buildinfo = authenticate_iso(str(iso_path), str(pubkey), check_full)
            if not success:
                print(f"\n[FAIL] {iso_name}: {err}")
                all_ok = False
            else:
                print(f"\n[OK] {iso_name} zweryfikowany poprawnie.")
                if buildinfo:
                    print(
                        f"      Wersja: {buildinfo['build_year']}.{buildinfo['build_week']}.{buildinfo['build_patch']}")
                    print(
                        f"      Wariant: {buildinfo['variant']}, Rynek: {buildinfo['market']}, MY: {buildinfo['model_year']}")

        if not all_ok:
            sys.exit("\nWeryfikacja NIE powiodła się dla wszystkich plików.")

        print("\n=== Wszystkie pliki ISO zweryfikowane pomyślnie ===")

        # Wyświetlenie manifestu (tylko jeśli mamy installer.iso)
        if not args.no_manifest and "installer.iso" in iso_files:
            print("\n--- Manifest ---")
            manifest_content = read_manifest_from_iso(iso_files["installer.iso"])
            if manifest_content:
                print(manifest_content)
            else:
                print("Nie znaleziono manifestu lub nie udało się go odczytać.")

    finally:
        # Posprzątaj – odmontuj wszystkie zamontowane obrazy
        for img in mounted_images:
            dismount_iso_powershell(img)


if __name__ == "__main__":
    main()