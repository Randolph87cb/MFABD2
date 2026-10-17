import argparse

from pycaw.pycaw import AudioUtilities


TARGET_PROCESS = "BrownDust II.exe"


def set_mute(muted: bool) -> int:
    changed = 0
    for session in AudioUtilities.GetAllSessions():
        try:
            process = session.Process
            if not process:
                continue
            process_name = process.name()
        except Exception as exc:  # noqa: BLE001 - stale Windows audio sessions are expected.
            print(f"skipped unavailable audio session: {exc!r}")
            continue

        if process_name.lower() == TARGET_PROCESS.lower():
            try:
                pid = process.pid
                volume = session.SimpleAudioVolume
                volume.SetMute(1 if muted else 0, None)
            except Exception as exc:  # noqa: BLE001 - the session may expire mid-enumeration.
                print(f"skipped unavailable {TARGET_PROCESS} audio session: {exc!r}")
                continue
            changed += 1
            print(f"{'muted' if muted else 'unmuted'} pid={pid} name={process_name}")
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
