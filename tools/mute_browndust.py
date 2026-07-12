import argparse

from pycaw.pycaw import AudioUtilities


TARGET_PROCESS = "BrownDust II.exe"


def set_mute(muted: bool) -> int:
    changed = 0
    for session in AudioUtilities.GetAllSessions():
        process = session.Process
        if not process:
            continue
        if process.name().lower() == TARGET_PROCESS.lower():
            volume = session.SimpleAudioVolume
            volume.SetMute(1 if muted else 0, None)
            changed += 1
            print(f"{'muted' if muted else 'unmuted'} pid={process.pid} name={process.name()}")
    return changed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--unmute", action="store_true", help="restore BrownDust II audio")
    args = parser.parse_args()

    changed = set_mute(not args.unmute)
    if not changed:
        raise SystemExit(f"audio session not found for {TARGET_PROCESS}")


if __name__ == "__main__":
    main()
