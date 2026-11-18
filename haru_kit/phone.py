"""``haru-kit phone``: connect a Twilio or Telnyx phone number to Haru in one command.

Uses only the Python standard library on the user's computer; the Pipecat WebSocket
bridge runs inside the same Modal container as Haru-4B so there is zero network hop
between the phone bridge and the model.
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from rich.markup import escape

from . import card as card_mod
from . import modal, paths, ui
from .detect import collect
from .procs import Failure
from .settings import Settings

E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")


@dataclass
class PhoneNumberInfo:
    provider: str  # "twilio" | "telnyx"
    id: str  # Twilio IncomingPhoneNumber SID or Telnyx phone_number ID
    phone_number: str
    label: str = ""
    in_use: str = ""  # human note when already bound to another webhook/trunk
    raw: dict | None = None


def compute_twilio_signature(url: str, params: dict[str, str], auth_token: str) -> str:
    """Compute Twilio's ``X-Twilio-Signature`` (HMAC-SHA1 base64)."""
    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    digest = hmac.new(auth_token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


def verify_twilio_signature(url: str, params: dict[str, str], signature: str, auth_token: str) -> bool:
    if not signature or not auth_token:
        return False
    expected = compute_twilio_signature(url, params, auth_token)
    return hmac.compare_digest(signature.encode("utf-8"), expected.encode("utf-8"))


def _state_path() -> Path:
    return paths.home_dir() / "phone_state.json"


def _creds_path() -> Path:
    return paths.home_dir() / "phone_credentials.json"


def _write_private_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
    tmp.replace(path)


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def running_text() -> str | None:
    """Return a one-line summary of the active ``haru-kit phone`` session, if any."""
    state = _read_json(_state_path())
    number = state.get("phone_number")
    provider = state.get("provider")
    if not number or not provider:
        return None
    if state.get("snapshot"):
        return f"{escape(str(number))} ({escape(str(provider))}, inbound active) · stop & restore: haru-kit phone stop"
    return f"{escape(str(number))} ({escape(str(provider))}) · stop: haru-kit phone stop"


# ---------------------------------------------------------- Carrier clients


class TwilioClient:
    BASE = "https://api.twilio.com/2010-04-01"

    def __init__(self, account_sid: str, auth_token: str) -> None:
        self.account_sid = account_sid.strip()
        self.auth_token = auth_token.strip()
        token = base64.b64encode(f"{self.account_sid}:{self.auth_token}".encode()).decode("ascii")
        self._headers = {"Authorization": f"Basic {token}", "Accept": "application/json"}

    def _request(self, method: str, path: str, form: dict[str, str] | None = None) -> dict:
        url = f"{self.BASE}/Accounts/{self.account_sid}{path}"
        body = None
        headers = dict(self._headers)
        if form is not None:
            body = urllib.parse.urlencode(form).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as err:
            detail = err.read().decode("utf-8", "replace")[:300]
            if err.code in (401, 403):
                raise Failure(
                    "Twilio rejected the credentials.",
                    f"HTTP {err.code}: {detail}",
                    "Check TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN.",
                ) from None
            raise Failure(f"Twilio API error (HTTP {err.code}).", detail, "Check your Twilio account.") from None
        except OSError as err:
            raise Failure("Could not reach Twilio.", str(err), "Check your internet connection.") from None

    def verify(self) -> None:
        self._request("GET", ".json")

    def list_numbers(self) -> list[PhoneNumberInfo]:
        data = self._request("GET", "/IncomingPhoneNumbers.json?PageSize=50")
        out: list[PhoneNumberInfo] = []
        for item in data.get("incoming_phone_numbers", []):
            in_use_parts = []
            if item.get("trunk_sid"):
                in_use_parts.append(f"SIP trunk {item['trunk_sid']}")
            elif item.get("voice_url"):
                in_use_parts.append("has voice webhook")
            out.append(
                PhoneNumberInfo(
                    provider="twilio",
                    id=item["sid"],
                    phone_number=item["phone_number"],
                    label=item.get("friendly_name") or "",
                    in_use=", ".join(in_use_parts),
                    raw=item,
                )
            )
        return out

    def buy_number(self, country: str = "US", area_code: str | None = None) -> PhoneNumberInfo:
        query = {"VoiceEnabled": "true", "PageSize": "5"}
        if area_code:
            query["AreaCode"] = area_code
        avail = self._request("GET", f"/AvailablePhoneNumbers/{country}/Local.json?{urllib.parse.urlencode(query)}")
        candidates = avail.get("available_phone_numbers") or []
        if not candidates:
            raise Failure(
                "No available Twilio phone numbers matched.",
                f"Country={country}, area_code={area_code or 'any'}.",
                "Try without --area-code or buy a number in the Twilio Console.",
            )
        chosen = candidates[0]["phone_number"]
        bought = self._request("POST", "/IncomingPhoneNumbers.json", {"PhoneNumber": chosen})
        return PhoneNumberInfo(
            provider="twilio",
            id=bought["sid"],
            phone_number=bought["phone_number"],
            label=bought.get("friendly_name") or "",
            raw=bought,
        )

    def configure_webhook(self, info: PhoneNumberInfo, voice_url: str) -> dict:
        raw = info.raw or self._request("GET", f"/IncomingPhoneNumbers/{info.id}.json")
        snapshot = {
            "provider": "twilio",
            "id": info.id,
            "phone_number": info.phone_number,
            "voice_url": raw.get("voice_url") or "",
            "voice_method": raw.get("voice_method") or "POST",
            "trunk_sid": raw.get("trunk_sid") or "",
        }
        self._request(
            "POST",
            f"/IncomingPhoneNumbers/{info.id}.json",
            {"VoiceUrl": voice_url, "VoiceMethod": "POST", "TrunkSid": ""},
        )
        return snapshot

    def restore_number(self, snapshot: dict) -> None:
        sid = snapshot.get("id")
        if not sid:
            return
        if snapshot.get("trunk_sid"):
            self._request("POST", f"/IncomingPhoneNumbers/{sid}.json", {"TrunkSid": snapshot["trunk_sid"]})
        else:
            self._request(
                "POST",
                f"/IncomingPhoneNumbers/{sid}.json",
                {
                    "VoiceUrl": snapshot.get("voice_url", ""),
                    "VoiceMethod": snapshot.get("voice_method", "POST"),
                    "TrunkSid": "",
                },
            )

    def place_call(self, *, to_number: str, from_number: str, stream_ws_url: str, bridge_token: str) -> str:
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response><Connect>"
            f'<Stream url="{xml_escape(stream_ws_url)}">'
            f'<Parameter name="bridge_token" value="{xml_escape(bridge_token)}"/>'
            '<Parameter name="speak_first" value="1"/>'
            "</Stream>"
            "</Connect></Response>"
        )
        res = self._request(
            "POST",
            "/Calls.json",
            {"To": to_number, "From": from_number, "Twiml": twiml},
        )
        return str(res.get("sid") or "")


class TelnyxClient:
    BASE = "https://api.telnyx.com/v2"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key.strip()
        self._headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        url = f"{self.BASE}{path}"
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=body, headers=self._headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as err:
            detail = err.read().decode("utf-8", "replace")[:350]
            if err.code in (401, 403):
                raise Failure(
                    "Telnyx rejected the API key.",
                    f"HTTP {err.code}: {detail}",
                    "Check TELNYX_API_KEY.",
                ) from None
            raise Failure(f"Telnyx API error (HTTP {err.code}).", detail, "Check your Telnyx account.") from None
        except OSError as err:
            raise Failure("Could not reach Telnyx.", str(err), "Check your internet connection.") from None

    def verify(self) -> None:
        self._request("GET", "/phone_numbers?page[size]=1")

    def list_numbers(self) -> list[PhoneNumberInfo]:
        data = self._request("GET", "/phone_numbers?page[size]=50")
        out: list[PhoneNumberInfo] = []
        for item in data.get("data", []):
            if item.get("status") not in (None, "active", "purchased"):
                continue
            conn = item.get("connection_name") or ""
            in_use = f"connected to {conn}" if conn and conn != "haru-kit" else ""
            out.append(
                PhoneNumberInfo(
                    provider="telnyx",
                    id=str(item["id"]),
                    phone_number=str(item["phone_number"]),
                    label=conn,
                    in_use=in_use,
                    raw=item,
                )
            )
        return out

    def buy_number(self, country: str = "US", area_code: str | None = None) -> PhoneNumberInfo:
        params: dict[str, str] = {"filter[country_code]": country, "filter[limit]": "5"}
        if area_code:
            params["filter[national_destination_code]"] = area_code
        avail = self._request("GET", f"/available_phone_numbers?{urllib.parse.urlencode(params)}")
        candidates = avail.get("data") or []
        if not candidates:
            raise Failure(
                "No available Telnyx phone numbers matched.",
                f"Country={country}, area_code={area_code or 'any'}.",
                "Try without --area-code or buy a number in the Telnyx Portal.",
            )
        chosen = candidates[0]["phone_number"]
        self._request("POST", "/number_orders", {"phone_numbers": [{"phone_number": chosen}]})
        # Fetch the newly provisioned phone_number record
        for _ in range(10):
            found = [n for n in self.list_numbers() if n.phone_number == chosen]
            if found:
                return found[0]
            time.sleep(1.5)
        raise Failure(
            f"Ordered {chosen}, waiting for Telnyx activation.",
            "Number order is still pending.",
            f"Run `haru-kit phone --telnyx --number {chosen}` in a minute.",
        )

    def _outbound_voice_profile_id(self) -> str:
        env_ovp = os.environ.get("TELNYX_OUTBOUND_VOICE_PROFILE_ID", "").strip()
        if env_ovp:
            return env_ovp
        profiles = self._request("GET", "/outbound_voice_profiles?page[size]=20").get("data") or []
        for prof in profiles:
            if prof.get("enabled", True) and prof.get("id"):
                return str(prof["id"])
        created = self._request("POST", "/outbound_voice_profiles", {"name": "haru-kit"}).get("data") or {}
        return str(created["id"])

    def ensure_texml_app(self, voice_url: str, name: str = "haru-kit") -> str:
        ovp_id = self._outbound_voice_profile_id()
        apps = self._request("GET", "/texml_applications?page[size]=50").get("data") or []
        for app in apps:
            if app.get("friendly_name") == name:
                app_id = str(app["id"])
                self._request(
                    "PATCH",
                    f"/texml_applications/{app_id}",
                    {
                        "friendly_name": name,
                        "voice_url": voice_url,
                        "voice_method": "POST",
                        "outbound": {"outbound_voice_profile_id": ovp_id},
                    },
                )
                return app_id
        created = self._request(
            "POST",
            "/texml_applications",
            {
                "friendly_name": name,
                "voice_url": voice_url,
                "voice_method": "POST",
                "outbound": {"outbound_voice_profile_id": ovp_id},
            },
        ).get("data") or {}
        return str(created["id"])

    def configure_webhook(self, info: PhoneNumberInfo, texml_app_id: str) -> dict:
        raw = info.raw or self._request("GET", f"/phone_numbers/{info.id}").get("data") or {}
        snapshot = {
            "provider": "telnyx",
            "id": info.id,
            "phone_number": info.phone_number,
            "connection_id": str(raw.get("connection_id") or ""),
        }
        self._request("PATCH", f"/phone_numbers/{info.id}", {"connection_id": texml_app_id})
        return snapshot

    def restore_number(self, snapshot: dict) -> None:
        num_id = snapshot.get("id")
        if not num_id:
            return
        prev_conn = snapshot.get("connection_id") or None
        self._request("PATCH", f"/phone_numbers/{num_id}", {"connection_id": prev_conn})

    def place_call(self, *, to_number: str, from_number: str, voice_url: str, texml_app_id: str) -> str:
        res = self._request(
            "POST",
            f"/texml/calls/{texml_app_id}",
            {"To": to_number, "From": from_number, "Url": voice_url, "UrlMethod": "POST"},
        )
        data = res.get("data") or res
        return str(data.get("call_sid") or data.get("call_control_id") or "")


# ----------------------------------------------------------- CLI helpers


def _choose_provider(args) -> str:
    if getattr(args, "twilio", False):
        return "twilio"
    if getattr(args, "telnyx", False):
        return "telnyx"
    saved = _read_json(_state_path()).get("provider") or _read_json(_creds_path()).get("last_provider")
    if getattr(args, "yes", False):
        if os.environ.get("TWILIO_ACCOUNT_SID") and not os.environ.get("TELNYX_API_KEY"):
            return "twilio"
        if os.environ.get("TELNYX_API_KEY") and not os.environ.get("TWILIO_ACCOUNT_SID"):
            return "telnyx"
        return saved if saved in ("twilio", "telnyx") else "twilio"
    rows = [
        ("Twilio", "connect a Twilio phone number over Media Streams", True),
        ("Telnyx", "connect a Telnyx phone number over TeXML Media Streams", True),
    ]
    default = 1 if saved == "telnyx" else 0
    return ("twilio", "telnyx")[ui.select("Which phone provider do you want to use?", rows, default)]


def _get_twilio_client(ask: bool) -> TwilioClient:
    saved = _read_json(_creds_path()).get("twilio") or {}
    sid = os.environ.get("TWILIO_ACCOUNT_SID") or saved.get("account_sid") or ""
    tok = os.environ.get("TWILIO_AUTH_TOKEN") or saved.get("auth_token") or ""
    if (not sid or not tok) and ask and ui.interactive():
        ui.console.print("  Enter your Twilio credentials (from https://console.twilio.com):")
        if not sid:
            sid = input("  Account SID (AC…): ").strip()
        if not tok:
            tok = getpass.getpass("  Auth Token: ").strip()
    if not sid or not tok:
        raise Failure(
            "Twilio credentials are missing.",
            "TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN were not found.",
            "Export TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN, or run `haru-kit phone --twilio` in a terminal.",
        )
    client = TwilioClient(sid, tok)
    client.verify()
    all_creds = _read_json(_creds_path())
    all_creds["twilio"] = {"account_sid": sid, "auth_token": tok}
    all_creds["last_provider"] = "twilio"
    _write_private_json(_creds_path(), all_creds)
    return client


def _get_telnyx_client(ask: bool) -> TelnyxClient:
    saved = _read_json(_creds_path()).get("telnyx") or {}
    key = os.environ.get("TELNYX_API_KEY") or saved.get("api_key") or ""
    if not key and ask and ui.interactive():
        ui.console.print("  Enter your Telnyx API key (from https://portal.telnyx.com/#/app/api-keys):")
        key = getpass.getpass("  Telnyx API Key (KEY…): ").strip()
    if not key:
        raise Failure(
            "Telnyx API key is missing.",
            "TELNYX_API_KEY was not found.",
            "Export TELNYX_API_KEY, or run `haru-kit phone --telnyx` in a terminal.",
        )
    client = TelnyxClient(key)
    client.verify()
    all_creds = _read_json(_creds_path())
    all_creds["telnyx"] = {"api_key": key}
    all_creds["last_provider"] = "telnyx"
    _write_private_json(_creds_path(), all_creds)
    return client


def _select_number(
    client: TwilioClient | TelnyxClient,
    *,
    requested_number: str | None,
    buy: bool,
    area_code: str | None,
    ask: bool,
) -> PhoneNumberInfo:
    if buy:
        with ui.Step("Buying a phone number"):
            bought = client.buy_number(area_code=area_code)
        ui.done("Bought phone number", bought.phone_number)
        return bought

    numbers = client.list_numbers()
    if requested_number:
        norm = requested_number.strip()
        for n in numbers:
            if n.phone_number == norm:
                return n
        raise Failure(
            f"Phone number {norm} was not found on this {client.__class__.__name__.removesuffix('Client')} account.",
            f"Found {len(numbers)} active number(s).",
            "Pass one of your account's numbers with --number, or pass --buy to purchase a new one.",
        )

    if not numbers:
        if ask and ui.confirm(
            "No phone numbers on this account yet. Buy a US number (~$1/month)?", yes="Buy", no="Cancel"
        ):
            return client.buy_number(area_code=area_code)
        raise Failure(
            "No phone numbers found on this account.",
            "You need at least one voice-capable phone number.",
            "Run again with `--buy` to purchase a number, or buy one in your provider's console.",
        )

    if not ask or not ui.interactive():
        return numbers[0]

    rows = []
    for item in numbers:
        note_parts = [p for p in (item.label, item.in_use) if p]
        rows.append((item.phone_number, " · ".join(note_parts) if note_parts else "available", True))
    rows.append(("Buy a new number", "about $1/month in your account", True))
    idx = ui.select("Which phone number should connect to Haru?", rows, 0)
    if idx == len(numbers):
        return client.buy_number(area_code=area_code)
    chosen = numbers[idx]
    if chosen.in_use and not ui.confirm(
        f"{chosen.phone_number} currently has ({chosen.in_use}). Point it to Haru for now? "
        "(`haru-kit phone stop` restores the previous setting.)",
        yes="Use this number",
        no="Cancel",
    ):
        raise KeyboardInterrupt
    return chosen


def _encode_gateway_audio_b64(voice_source) -> tuple[str, str]:
    import soundfile as sf

    from .runtime.api_server import SAMPLE_RATE, decode_audio_bytes, trim_and_cap_reference

    if isinstance(voice_source, Path):
        samples = decode_audio_bytes(voice_source.read_bytes(), SAMPLE_RATE)
    elif isinstance(voice_source, (bytes, bytearray)):
        samples = decode_audio_bytes(bytes(voice_source), SAMPLE_RATE)
    else:
        import numpy as np

        samples = np.asarray(voice_source, dtype=np.float32).reshape(-1)
    trimmed = trim_and_cap_reference(samples, SAMPLE_RATE)
    if trimmed.size == 0:
        trimmed = samples
    buf = io.BytesIO()
    sf.write(buf, trimmed, SAMPLE_RATE, format="FLAC", subtype="PCM_16")
    return base64.b64encode(buf.getvalue()).decode("ascii"), ".flac"


def _gateway_ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/api/ready", timeout=5) as resp:
            return bool(json.load(resp).get("ready"))
    except Exception:  # noqa: BLE001
        return False


def _ensure_phone_gateway(
    args,
    *,
    provider: str,
    twilio_client: TwilioClient | None,
    telnyx_client: TelnyxClient | None,
    phone_number: str,
    speak_first: bool,
) -> tuple[str, str]:
    """Return ``(gateway_https_url, bridge_token)`` — reusing an active phone gateway when possible."""
    state = _read_json(_state_path())
    if (
        state.get("gateway_url")
        and state.get("bridge_token")
        and state.get("provider") == provider
        and _gateway_ready(state["gateway_url"])
        and not getattr(args, "redeploy", False)
    ):
        return state["gateway_url"], state["bridge_token"]

    facts = collect()
    settings = Settings.load()
    # Default US (+1) phone calls to the "us" Modal region so phone audio doesn't cross oceans.
    if getattr(args, "region", None):
        settings.modal_region = args.region
    elif phone_number.startswith("+1") and settings.modal_region == "auto":
        settings.modal_region = "us"

    prompt_text, voice_source = card_mod.resolve_card_and_voice(
        getattr(args, "card", None) or getattr(args, "character", "haru"),
        getattr(args, "voice", None),
        language=getattr(args, "lang", "en"),
    )
    voice_b64, voice_ext = _encode_gateway_audio_b64(voice_source)
    preroll_b64 = None
    if getattr(args, "preroll", None):
        preroll_b64, _ = _encode_gateway_audio_b64(Path(args.preroll))

    api_key = "hk_" + secrets.token_urlsafe(24)
    bridge_token = secrets.token_urlsafe(24)
    options: dict = {
        "mode": "phone",
        "bridge_token": bridge_token,
        "max_sessions": getattr(args, "max_calls", None) or 2,
        "character": getattr(args, "character", None) or "haru",
        "language": getattr(args, "lang", None) or "en",
        "card_text": prompt_text,
        "voice_b64": voice_b64,
        "voice_ext": voice_ext,
        "speak_first": speak_first,
    }
    if preroll_b64:
        options["preroll_b64"] = preroll_b64
    if twilio_client is not None:
        options["twilio_sid"] = twilio_client.account_sid
        options["twilio_token"] = twilio_client.auth_token
    if telnyx_client is not None:
        options["telnyx_key"] = telnyx_client.api_key

    gpu_type = (getattr(args, "gpu", None) or modal.GPU).upper()
    started = modal.start_tunnel(
        facts,
        settings,
        args.verbose,
        ask=not args.yes,
        function="serve_gateway",
        key=api_key,
        options=options,
        gpu_override=gpu_type,
        retry_cmd=f"haru-kit phone --{provider}",
    )
    if started is None:
        raise KeyboardInterrupt
    url, _, _, _ = started
    return url.rstrip("/"), bridge_token


def restore_saved_number(*, quiet: bool = False) -> bool:
    """Restore any inbound phone number webhook/trunk saved in ``phone_state.json``."""
    state = _read_json(_state_path())
    snapshot = state.get("snapshot")
    if not snapshot:
        _state_path().unlink(missing_ok=True)
        return False
    provider = snapshot.get("provider")
    try:
        if provider == "twilio":
            client = _get_twilio_client(ask=False)
            client.restore_number(snapshot)
        elif provider == "telnyx":
            client = _get_telnyx_client(ask=False)
            client.restore_number(snapshot)
        if not quiet:
            ui.done("Restored phone number setting", snapshot.get("phone_number", ""))
    except Failure as err:
        if not quiet:
            ui.error("Could not restore previous phone number setting.", err.why, err.fix)
        return False
    state.pop("snapshot", None)
    _state_path().unlink(missing_ok=True)
    return True


def phone_stop_cmd(_args) -> int:
    restore_saved_number(quiet=False)
    try:
        modal.stop()
    except Failure as error:
        ui.error(error.what, error.why, error.fix)
        return 1
    return 0


def phone_call_cmd(args) -> int:
    to_number = args.to_number.strip()
    if not E164_RE.fullmatch(to_number):
        ui.error(
            "Invalid destination phone number.",
            f"Expected E.164 format starting with '+', got {to_number!r}.",
            "Example: haru-kit phone call +14155550199",
        )
        return 1

    ask = not args.yes
    try:
        provider = _choose_provider(args)
        twilio_client = _get_twilio_client(ask) if provider == "twilio" else None
        telnyx_client = _get_telnyx_client(ask) if provider == "telnyx" else None
        carrier = twilio_client or telnyx_client
        assert carrier is not None

        from_number = getattr(args, "from_number", None)
        if not from_number:
            saved_num = _read_json(_state_path()).get("phone_number")
            if saved_num and _read_json(_state_path()).get("provider") == provider:
                from_number = saved_num
            else:
                chosen = _select_number(carrier, requested_number=None, buy=False, area_code=None, ask=ask)
                from_number = chosen.phone_number

        gateway_url, bridge_token = _ensure_phone_gateway(
            args,
            provider=provider,
            twilio_client=twilio_client,
            telnyx_client=telnyx_client,
            phone_number=from_number,
            speak_first=True,
        )

        with ui.Step(f"Dialing {to_number} from {from_number}"):
            if twilio_client is not None:
                ws_url = gateway_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/twilio/ws"
                call_id = twilio_client.place_call(
                    to_number=to_number,
                    from_number=from_number,
                    stream_ws_url=ws_url,
                    bridge_token=bridge_token,
                )
            else:
                assert telnyx_client is not None
                qs = urllib.parse.urlencode({"token": bridge_token, "speak_first": "1"})
                voice_url = f"{gateway_url}/telnyx/voice?{qs}"
                app_id = telnyx_client.ensure_texml_app(voice_url)
                call_id = telnyx_client.place_call(
                    to_number=to_number,
                    from_number=from_number,
                    voice_url=voice_url,
                    texml_app_id=app_id,
                )

        state = _read_json(_state_path())
        state.update({
            "provider": provider,
            "phone_number": from_number,
            "gateway_url": gateway_url,
            "bridge_token": bridge_token,
        })
        _write_private_json(_state_path(), state)

        ui.done("Call placed", f"{from_number} → {to_number} ({provider}, id {escape(call_id)})")
        ui.console.print("    Answer the phone to talk to Haru.")
        ui.console.print("    Stop the GPU when finished: haru-kit phone stop")
        return 0
    except Failure as error:
        ui.console.print()
        ui.error(error.what, error.why, error.fix)
        return 1
    except KeyboardInterrupt:
        ui.console.print("\n  Cancelled.")
        return 130


def phone_connect_cmd(args) -> int:
    ask = not args.yes
    ui.header()
    try:
        provider = _choose_provider(args)
        twilio_client = _get_twilio_client(ask) if provider == "twilio" else None
        telnyx_client = _get_telnyx_client(ask) if provider == "telnyx" else None
        carrier = twilio_client or telnyx_client
        assert carrier is not None

        info = _select_number(
            carrier,
            requested_number=args.number,
            buy=args.buy,
            area_code=args.area_code,
            ask=ask,
        )

        # Restore any previously connected number before wiring a new one.
        restore_saved_number(quiet=True)

        gateway_url, bridge_token = _ensure_phone_gateway(
            args,
            provider=provider,
            twilio_client=twilio_client,
            telnyx_client=telnyx_client,
            phone_number=info.phone_number,
            speak_first=args.speak_first,
        )

        with ui.Step(f"Connecting {info.phone_number}"):
            qs = urllib.parse.urlencode({"token": bridge_token, "speak_first": "1" if args.speak_first else "0"})
            if twilio_client is not None:
                voice_url = f"{gateway_url}/twilio/voice?{qs}"
                snapshot = twilio_client.configure_webhook(info, voice_url)
            else:
                assert telnyx_client is not None
                voice_url = f"{gateway_url}/telnyx/voice?{qs}"
                app_id = telnyx_client.ensure_texml_app(voice_url)
                snapshot = telnyx_client.configure_webhook(info, app_id)

        _write_private_json(
            _state_path(),
            {
                "provider": provider,
                "phone_number": info.phone_number,
                "gateway_url": gateway_url,
                "bridge_token": bridge_token,
                "snapshot": snapshot,
            },
        )

        ui.console.print()
        ui.done("Phone connected to Haru", f"{info.phone_number} ({provider})")
        ui.console.print(f"    Call {escape(info.phone_number)} from any phone to talk to Haru.")
        ui.console.print(f"    Or place an outbound call: haru-kit phone call +15551234567 --{provider}")
        ui.console.print("    Stop GPU & restore number: haru-kit phone stop")
        ui.console.print()
        return 0
    except Failure as error:
        ui.console.print()
        ui.error(error.what, error.why, error.fix)
        return 1
    except KeyboardInterrupt:
        ui.console.print("\n  Cancelled.")
        return 130


def phone_cmd(args) -> int:
    action = getattr(args, "phone_action", None)
    if action == "stop":
        return phone_stop_cmd(args)
    if action == "call":
        return phone_call_cmd(args)
    return phone_connect_cmd(args)
