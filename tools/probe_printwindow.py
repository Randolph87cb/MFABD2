import ctypes
from ctypes import wintypes
from pathlib import Path

from PIL import Image

from win32_dpi import enable_dpi_awareness


enable_dpi_awareness()


user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

PW_RENDERFULLCONTENT = 0x00000002
SRCCOPY = 0x00CC0020


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", wintypes.DWORD * 3),
    ]


def save_hwnd(hwnd: int, out_path: Path) -> bool:
    rect = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise RuntimeError("GetWindowRect failed")

    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width <= 0 or height <= 0:
        raise RuntimeError(f"Invalid window size: {width}x{height}")

    window_dc = user32.GetWindowDC(hwnd)
    mem_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    old_obj = gdi32.SelectObject(mem_dc, bitmap)

    ok = bool(user32.PrintWindow(hwnd, mem_dc, PW_RENDERFULLCONTENT))
    if not ok:
        # Some DirectX windows refuse PrintWindow but still allow a BitBlt fallback
        # when visible. Saving it helps distinguish API failure from black output.
        gdi32.BitBlt(mem_dc, 0, 0, width, height, window_dc, 0, 0, SRCCOPY)

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = width
    bmi.bmiHeader.biHeight = -height
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0

    buffer_size = width * height * 4
    buffer = ctypes.create_string_buffer(buffer_size)
    rows = gdi32.GetDIBits(
        mem_dc,
        bitmap,
        0,
        height,
        buffer,
        ctypes.byref(bmi),
        0,
    )
    if rows == 0:
        raise RuntimeError("GetDIBits failed")

    image = Image.frombuffer("RGBA", (width, height), buffer, "raw", "BGRA", 0, 1)
    image.save(out_path)

    gdi32.SelectObject(mem_dc, old_obj)
    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(mem_dc)
    user32.ReleaseDC(hwnd, window_dc)
    return ok


def main() -> None:
    hwnd = user32.FindWindowW(None, "BrownDust II")
    if not hwnd:
        raise SystemExit("BrownDust II window not found")

    out_path = Path.cwd() / "printwindow_probe.png"
    ok = save_hwnd(hwnd, out_path)
    print(f"hwnd=0x{hwnd:X}")
    print(f"printwindow_ok={ok}")
    print(f"output={out_path}")


if __name__ == "__main__":
    main()
