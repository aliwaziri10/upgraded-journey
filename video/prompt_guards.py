"""
TechPulse - shared prompt-safety guard text for Agnes video generation.

Ported 2026-09-06 from Marius's prompt_builder.py, where both guards are
confirmed live in production (QUALITY_GUARD since 2026-08-22,
CROWD_ANATOMY_SAFETY_GUARD since 2026-09-06). TechPulse's video prompts
are written directly by Gemini in script/generate_script.py and sent
straight to Agnes in video/generate_video.py with no safety guard text
added - these two constants close that gap. Anachronism/distinct-
individuals/motion-continuity guards from Marius are NOT ported here,
since TechPulse covers modern-day tech/AI/science news (no historical
setting to protect, and stories are rarely person-centric group scenes
the way Marius's are) - only the two guards addressing TechPulse's
actual reported issues (waxy AI skin, cloned crowd faces) are included.
"""

# FIX 1 - WAXY/PLASTIC AI SKIN, prompt half (2026-09-06): concrete
# texture language (pore detail, imperfections, matte finish) rather
# than only negative instructions - generation models respond more
# reliably to being told what TO render than only what to avoid.
QUALITY_GUARD = (
    "modern high-end digital cinema, crisp sharp clarity, professional color grading, "
    "shallow depth of field, cinematic lighting, vivid saturated color, no sepia tone, "
    "no heavy desaturation, no muted documentary color grading, no grainy vintage film look, "
    "natural realistic human skin with visible pore texture and natural skin imperfections, "
    "matte skin finish, not glossy, not waxy, not airbrushed, not overly smooth, no beauty-filter look, "
    "no artificial CGI look, no flat synthetic AI look, no plastic skin, no doll-like skin, "
    "no candy-coated or glazed look, photographically real, not illustrated, not animated, not stylized"
)

# FIX 2 - CLONED/IDENTICAL CROWD FACES (2026-09-06): models default to
# duplicated "copy-paste" faces in crowds unless structurally prevented -
# a negative instruction alone is known-unreliable by itself, so this
# caps how many sharp, individuated faces appear in the foreground and
# pushes any remaining crowd into an out-of-focus background instead,
# same structural fix already confirmed live on Marius and Nova.
CROWD_ANATOMY_SAFETY_GUARD = (
    "if this shot contains a crowd or group larger than 4-5 people, only 4-5 "
    "of them are sharp, individuated, foreground figures with distinct faces "
    "and clothing - everyone beyond that is rendered soft-focus, out-of-focus, "
    "or partially obscured in the background, never as additional sharp "
    "duplicate faces. Do not render a large crowd as a uniform wall of "
    "identical, equally-sharp people"
)
