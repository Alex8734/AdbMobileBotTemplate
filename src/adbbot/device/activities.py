"""Android-Activities erkennen: Werbe-SDKs laufen in eigenen Activities, meist im Package der App."""

from __future__ import annotations

# Activities bekannter Werbe-SDKs (Teilstrings, ohne Groß-/Kleinschreibung)
AD_ACTIVITY_PATTERNS = (
    "com.google.android.gms.ads",  # AdMob
    "com.unity3d.services.ads",
    "com.unity3d.ads",
    "com.applovin",
    "com.ironsource",
    "com.vungle",
    "com.liftoff",
    "com.mbridge",  # Mintegral
    "com.mintegral",
    "com.bytedance.sdk.openadsdk",  # Pangle
    "com.facebook.ads",
    "com.chartboost",
    "com.inmobi",
    "com.fyber",
    "com.adcolony",
    "com.digitalturbine",
    "com.moloco",
    "com.bigossp",
    "com.yandex.mobile.ads",
    "adactivity",
    "interstitial",
    "rewarded",
    "fullscreenad",
)


def is_ad_activity(activity: str | None) -> bool:
    if not activity:
        return False
    lowered = activity.lower()
    return any(pattern in lowered for pattern in AD_ACTIVITY_PATTERNS)


def full_activity_name(package: str, activity: str) -> str:
    """`.MainActivity` → `com.example.app.MainActivity` (so liefert `current_activity()` den Namen)."""
    return f"{package}{activity}" if activity.startswith(".") else activity
