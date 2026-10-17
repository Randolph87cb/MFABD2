import argparse

from pycaw.pycaw import AudioSession, AudioUtilities, IAudioSessionControl2


TARGET_PROCESS = "BrownDust II.exe"
MISSING_AUDIO_ENDPOINT_HRESULT = -2147023728


def _is_missing_audio_endpoint(exc: Exception) -> bool:
    hresult = getattr(exc, "hresult", None)
    if hresult is None and exc.args:
        hresult = exc.args[0]
    return hresult == MISSING_AUDIO_ENDPOINT_HRESULT


def _get_audio_sessions() -> list[AudioSession] | None:
    sessions: list[AudioSession] = []
    try:
        manager = AudioUtilities.GetAudioSessionManager()
        if manager is None:
            return None
        enumerator = manager.GetSessionEnumerator()
        count = enumerator.GetCount()
    except Exception as exc:  # noqa: BLE001 - preserve pycaw errors for the caller.
        if _is_missing_audio_endpoint(exc):
            print("no default Windows audio output endpoint is available")
            return None
        raise

    for index in range(count):
        try:
            control = enumerator.GetSession(index)
            if control is None:
                continue
            control2 = control.QueryInterface(IAudioSessionControl2)
            if control2 is not None:
                sessions.append(AudioSession(control2))
        except Exception as exc:  # noqa: BLE001 - sessions can expire during enumeration.
            print(f"skipped unavailable audio session index={index}: {exc!r}")
    return sessions


def set_mute(muted: bool) -> int | None:
    changed = 0
    sessions = _get_audio_sessions()
    if sessions is None:
        return None

    for session in sessions:
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
    if changed is None:
        return
    if not changed:
        raise SystemExit(f"audio session not found for {TARGET_PROCESS}")


if __name__ == "__main__":
    main()
