"""
English / Spanish (Spain) text for the doctor-facing pages and alert emails.

English is the source language: every string in the templates and in app.py is
written in English and wrapped in _() (templates) or t() (Python). The English
text itself is the lookup key, so a string missing from ES simply shows in
English rather than breaking the page -- and `python check_i18n.py` lists any
such gaps.

Placeholders use str.format names, e.g. "Updated {when}"; the Spanish entry
must keep the same names. Strings containing <b> are rendered with |safe in the
templates, so keep their markup identical in both languages.

Alert emails are translated as whole messages (EMAILS below) rather than
sentence by sentence, because word order differs between the two languages.
"""
from datetime import datetime

LANGS = ("en", "es")
DEFAULT_LANG = "en"
# What the language switch and the settings dropdown show.
LANGUAGE_NAMES = (("en", "English"), ("es", "Español (España)"))
SWITCH_NAMES = (("en", "EN"), ("es", "ES"))
# For the browser's number formatting (Intl / Chart.js).
LOCALES = {"en": "en-GB", "es": "es-ES"}
# strftime patterns. Spain writes day before month.
DATE_FMT = {"en": "%Y-%m-%d %H:%M", "es": "%d/%m/%Y %H:%M"}
DAY_FMT  = {"en": "%m-%d %H:%M",    "es": "%d/%m %H:%M"}

# Names shown for the status and alert codes. The codes themselves (stored in
# alert_state, used as CSS classes) never change.
ALERT_NAMES = {
    "en": {"OK": "OK", "ALERT": "ALERT", "SILENT": "SILENT", "LOW": "LOW",
           "CADENCE": "CADENCE", "MINUTES": "MINUTES", "SPEED": "SPEED",
           "BATTERY": "BATTERY"},
    "es": {"OK": "OK", "ALERT": "ALERTA", "SILENT": "SIN DATOS",
           "LOW": "POCOS PASOS", "CADENCE": "CADENCIA", "MINUTES": "POCO TIEMPO ACTIVO",
           "SPEED": "VELOCIDAD", "BATTERY": "BATERÍA"},
}

ES = {
    # --- login
    "Sign in": "Iniciar sesión",
    "Authorised clinical access only": "Acceso exclusivo para personal clínico autorizado",
    "User": "Usuario",
    "Password": "Contraseña",
    "Invalid credentials": "Usuario o contraseña incorrectos",

    # --- shared navigation
    "Settings": "Ajustes",
    "Sign out": "Cerrar sesión",
    "Dashboard": "Panel",
    "All patients": "Todos los pacientes",
    "Back to visualization": "Volver a la visualización",

    # --- dashboard
    "Patient Monitor": "Monitor de pacientes",
    "Updated {when}": "Actualizado {when}",
    "Alert rules:": "Reglas de alerta:",
    "if no data for > {hours} h": "si no llegan datos durante > {hours} h",
    "if latest daily steps < each patient's own threshold":
        "si los pasos del último día < el umbral propio de cada paciente",
    "if measured walking speed < each patient's own threshold":
        "si la velocidad de marcha medida < el umbral propio de cada paciente",
    "if battery < {pct}%.": "si la batería < {pct} %.",
    "steps · last day (target {n})": "pasos · último día (objetivo {n})",
    "walking speed": "velocidad de marcha",
    "stance": "apoyo",
    "battery": "batería",
    "last seen {when} ({hours} h ago)": "último dato {when} (hace {hours} h)",
    "No data received yet. Point a device (or seed_demo.py) at this server.":
        "Aún no se han recibido datos. Configure un dispositivo (o seed_demo.py) para enviarlos a este servidor.",

    # --- settings
    "Email alerts": "Alertas por correo electrónico",
    "When a patient sends no data or activity is low, an email is sent here.":
        "Cuando un paciente deja de enviar datos o su actividad es baja, se envía un correo a esta dirección.",
    "Send alerts to (doctor's email)": "Enviar alertas a (correo del médico)",
    "doctor@hospital.org": "medico@hospital.es",
    "From address (optional)": "Dirección del remitente (opcional)",
    "Alert email language": "Idioma de los correos de alerta",
    "Independent of the language you view this page in, so the doctors can receive Spanish alerts while you work in English.":
        "Es independiente del idioma en que vea esta página: los médicos pueden recibir las alertas en español aunque usted trabaje en inglés.",
    "Mail server (SMTP)": "Servidor de correo (SMTP)",
    "Any SMTP provider works — e.g. Brevo, SendGrid, your hospital's mail server.":
        "Sirve cualquier proveedor SMTP: por ejemplo Brevo, SendGrid o el servidor de correo de su hospital.",
    "SMTP host": "Servidor SMTP",
    "Port": "Puerto",
    "SMTP username": "Usuario SMTP",
    "SMTP password / key": "Contraseña / clave SMTP",
    "•••••••• (leave blank to keep)": "•••••••• (déjelo en blanco para conservarla)",
    "enter password / API key": "introduzca la contraseña / clave API",
    "Stored on the server only; never shown back in the page.":
        "Se guarda solo en el servidor; nunca se vuelve a mostrar en la página.",
    "Alert thresholds": "Umbrales de alerta",
    "Alert after no data for (hours)": "Alertar tras no recibir datos durante (horas)",
    "Low-battery threshold (%)": "Umbral de batería baja (%)",
    "The daily-step threshold is set per patient on each patient's page.":
        "El umbral de pasos diarios se configura para cada paciente en su propia página.",
    "Save settings": "Guardar ajustes",
    "Send test email": "Enviar correo de prueba",
    "Patient labels (MAC → name)": "Etiquetas de pacientes (MAC → nombre)",
    "Give each device that has sent data a friendly label shown on the dashboard (e.g. <b>User1</b>). The MAC address stays visible inside each patient's page. Leave blank to just show the MAC. <b>Keep labels non-identifying</b> (no real names) to preserve pseudonymisation.":
        "Asigne a cada dispositivo que haya enviado datos una etiqueta que se mostrará en el panel (p. ej. <b>Usuario1</b>). La dirección MAC sigue visible en la página de cada paciente. Déjelo en blanco para mostrar solo la MAC. <b>Use etiquetas que no identifiquen al paciente</b> (sin nombres reales) para mantener la seudonimización.",
    "e.g. User{n}": "p. ej. Usuario{n}",
    "Save labels": "Guardar etiquetas",
    "No devices have sent data yet.": "Ningún dispositivo ha enviado datos todavía.",

    # --- per-patient thresholds
    "Activity thresholds for this patient": "Umbrales de actividad de este paciente",
    "An alert (and email) is raised when the latest day falls below any of these. Set a value to <b>0</b> to disable that metric.":
        "Se genera una alerta (y un correo) cuando el último día queda por debajo de cualquiera de estos valores. Ponga <b>0</b> para desactivar esa métrica.",
    "Minimum steps per day": "Mínimo de pasos al día",
    "Total steps on the day — raises a {alert} alert if below.":
        "Pasos totales del día: genera una alerta {alert} si quedan por debajo.",
    "Minimum cadence (steps/min while walking)": "Cadencia mínima (pasos/min al caminar)",
    "The cadence shown on the patient page: measured from the gait cycle when the day has gait data, otherwise estimated from the fully walked minutes of bouts lasting 3 min or more. A day with neither raises no cadence alert.":
        "Es la cadencia que muestra la página del paciente: medida a partir del ciclo de marcha cuando el día tiene datos de marcha y, si no, estimada a partir de los minutos caminados por completo en episodios de 3 min o más. Un día sin ninguno de los dos no genera alerta de cadencia.",
    "Minimum active minutes per day": "Mínimo de minutos activos al día",
    "Number of minutes in the day with any walking recorded.":
        "Número de minutos del día en los que se registró algo de marcha.",
    "Minimum walking speed (m/s)": "Velocidad de marcha mínima (m/s)",
    "Measured from the gait phases, not from step count. Only days on which the device actually measured gait can raise this alert, so a device on step-only firmware never will. Depends on the device's per-patient stride-length calibration — check that before setting a tight threshold.":
        "Se mide a partir de las fases de la marcha, no del número de pasos. Solo los días en que el dispositivo midió realmente la marcha pueden generar esta alerta, así que un dispositivo con firmware solo de pasos nunca lo hará. Depende de la calibración de longitud de zancada de cada paciente en el dispositivo: compruébela antes de fijar un umbral ajustado.",
    "Save thresholds": "Guardar umbrales",

    # --- patient page
    "steps": "pasos",
    "selected day": "día seleccionado",
    "cadence (steps/min)": "cadencia (pasos/min)",
    "active minutes": "minutos activos",
    "walking speed (m/s)": "velocidad de marcha (m/s)",
    "stance phase (% of cycle)": "fase de apoyo (% del ciclo)",
    "since last report": "desde el último envío",
    "steps/day": "pasos/día",
    "cadence": "cadencia",
    "active min": "min activos",
    "speed": "velocidad",
    "off": "desactivado",
    "edit in ⚙ Settings": "editar en ⚙ Ajustes",
    "Daily steps": "Pasos diarios",
    "(click any day to see its detail)": "(pulse sobre un día para ver su detalle)",
    "Steps per minute": "Pasos por minuto",
    "Active time": "Tiempo activo",
    "Walking bouts": "Episodios de marcha",
    "Bouts": "Episodios",
    "Minutes": "Minutos",
    "Share of walking time": "% del tiempo de marcha",
    "Steps": "Pasos",
    "Steps per bout": "Pasos por episodio",
    "mean ± SD": "media ± DE",
    "Cadence (steps/min)": "Cadencia (pasos/min)",
    "All walking": "Toda la marcha",
    "{n} min or less": "{n} min o menos",
    "{a} to {b} min": "de {a} a {b} min",
    "{n} min or more": "{n} min o más",
    "A bout is a run of consecutive minutes with steps recorded; the bar shows how the day's walking time splits between the three active times. Cadence here is the mean and standard deviation of the step count of every minute walked in that group; steps per bout is the same over each bout's total. Cadence reads low in the short bouts because those minutes are only partly walked — it is not slower walking, which is why the day's cadence at the top is never an average over them.":
        "Un episodio es una serie de minutos consecutivos con pasos registrados; la barra muestra cómo se reparte el tiempo de marcha del día entre los tres tiempos activos. Aquí la cadencia es la media y la desviación estándar del número de pasos de cada minuto caminado en ese grupo; los pasos por episodio son lo mismo sobre el total de cada episodio. La cadencia sale baja en los episodios cortos porque esos minutos solo se caminan en parte; no indica una marcha más lenta, y por eso la cadencia del día que aparece arriba nunca es una media sobre ellos.",
    "No walking recorded on this day.": "No se registró marcha este día.",
    "Gait cycle": "Ciclo de marcha",
    "STANCE": "APOYO",
    "SWING": "OSCILACIÓN",
    "heel strike": "contacto del talón",
    "toe off": "despegue de los dedos",
    "next heel strike": "siguiente contacto del talón",
    "stride time (s)": "tiempo de zancada (s)",
    "stride length (cm)": "longitud de zancada (cm)",
    "stride variability (CV %)": "variabilidad de la zancada (CV %)",
    "shank excursion (°)": "excursión angular de la pierna (°)",
    "gait cycles analysed": "ciclos de marcha analizados",
    "Stance, swing, cadence, stride time and variability are measured directly from the heel-strike and toe-off events in the shank gyroscope. <b>Stride length and walking speed are modelled</b> from the shank excursion and depend on this patient's stride-length calibration on the device — check it before reading anything into an unexpected value.":
        "El apoyo, la oscilación, la cadencia, el tiempo de zancada y su variabilidad se miden directamente a partir de los eventos de contacto del talón y despegue de los dedos en el giroscopio de la pierna. <b>La longitud de zancada y la velocidad de marcha se estiman con un modelo</b> a partir de la excursión angular de la pierna y dependen de la calibración de longitud de zancada de este paciente en el dispositivo: compruébela antes de sacar conclusiones de un valor inesperado.",
    "No gait data for this day. The device measures gait in short bursts while the patient is walking, so a day with little walking — or a device running step-only firmware — records none.":
        "No hay datos de marcha para este día. El dispositivo mide la marcha en ráfagas cortas mientras el paciente camina, así que un día con poca marcha (o un dispositivo con firmware solo de pasos) no registra ninguno.",
    "Walking speed (m/s)": "Velocidad de marcha (m/s)",
    "Gaps are minutes the device did not measure, not minutes standing still.":
        "Los huecos son minutos que el dispositivo no midió, no minutos en reposo.",
    "Delete all data for this device (GDPR erasure)":
        "Borrar todos los datos de este dispositivo (supresión según el RGPD)",
    "Delete ALL data for {device}? This cannot be undone (GDPR erasure).":
        "¿Borrar TODOS los datos de {device}? No se puede deshacer (supresión según el RGPD).",
    "steps/min": "pasos/min",
    "in a {n}-min walking bout ({group})": "en un episodio de marcha de {n} min ({group})",
    "hour of day": "hora del día",
    "not measured": "sin medir",
    "Gait cycle on {day}": "Ciclo de marcha del {day}",
    "Walking bouts on {day}": "Episodios de marcha del {day}",
    "{group}: {min} min ({pct} of walking time)": "{group}: {min} min ({pct} del tiempo de marcha)",
    "Share of walking time: {parts}": "Reparto del tiempo de marcha: {parts}",
    "Steps per minute on {day}": "Pasos por minuto del {day}",
    "Walking speed on {day} (m/s)": "Velocidad de marcha del {day} (m/s)",
    "measured from gait": "medida a partir de la marcha",
    "estimated from step counts": "estimada a partir de los pasos",
    "no steady walking to read it from": "sin marcha continuada de la que obtenerla",

    # --- flash messages
    "Settings saved": "Ajustes guardados",
    "Patient labels saved.": "Etiquetas de pacientes guardadas.",
    "Test email sent: {msg}": "Correo de prueba enviado: {msg}",
    "Test failed: {msg}": "Fallo en la prueba: {msg}",
    "Sent to {to}": "Enviado a {to}",
    "Email not configured (set SMTP host and recipient in Settings).":
        "El correo no está configurado (indique el servidor SMTP y el destinatario en Ajustes).",
    "Invalid value — thresholds must be numbers ≥ 0 (0 disables a metric).":
        "Valor no válido: los umbrales deben ser números ≥ 0 (0 desactiva una métrica).",
    "Thresholds saved.": "Umbrales guardados.",
    "All data for {device} deleted": "Se han borrado todos los datos de {device}",
}


# Alert and test emails, as whole messages: {kind: {lang: (subject, body)}}.
# Fields available to every alert: patient, hours, silent_hours, last_seen,
# battery, battery_min, cadence, cadence_source, min_cadence, speed, min_speed,
# cycles, stance, gait_cadence, active_minutes, min_minutes, steps, min_steps.
EMAILS = {
    "TEST": {
        "en": ("[StepCounter] Test email",
               "This is a test from the StepCounter server. If you can read this, "
               "patient alerts will reach this inbox."),
        "es": ("[StepCounter] Correo de prueba",
               "Esto es una prueba del servidor StepCounter. Si puede leerlo, las "
               "alertas de los pacientes llegarán a este buzón."),
    },
    "SILENT": {
        "en": ("[StepCounter] Alert: patient {patient} has not walked",
               "Patient: {patient}\n\n"
               "This patient has not walked (no data received) for {hours} h, exceeding "
               "the configured limit of {silent_hours} h.\n"
               "Last data received: {last_seen}.\n\n"
               "The device may be unworn, the battery empty, or the patient immobile."),
        "es": ("[StepCounter] Alerta: el paciente {patient} no ha caminado",
               "Paciente: {patient}\n\n"
               "Este paciente no ha caminado (no se han recibido datos) en {hours} h, más "
               "que el límite configurado de {silent_hours} h.\n"
               "Último dato recibido: {last_seen}.\n\n"
               "Puede que no lleve puesto el dispositivo, que la batería esté agotada o "
               "que el paciente esté inmovilizado."),
    },
    "BATTERY": {
        "en": ("[StepCounter] Alert: patient {patient} low battery",
               "Patient: {patient}\n\n"
               "The device battery is at {battery}%, below the configured threshold of "
               "{battery_min}%.\n"
               "Last data received: {last_seen}.\n\n"
               "The device should be recharged soon to avoid a gap in monitoring."),
        "es": ("[StepCounter] Alerta: batería baja del paciente {patient}",
               "Paciente: {patient}\n\n"
               "La batería del dispositivo está al {battery} %, por debajo del umbral "
               "configurado del {battery_min} %.\n"
               "Último dato recibido: {last_seen}.\n\n"
               "Conviene recargar pronto el dispositivo para no interrumpir el "
               "seguimiento."),
    },
    "CADENCE": {
        "en": ("[StepCounter] Alert: patient {patient} low cadence",
               "Patient: {patient}\n\n"
               "Walking cadence was {cadence} steps/min on the latest day "
               "({cadence_source}), below this patient's threshold of {min_cadence} "
               "steps/min.\n"
               "Last data received: {last_seen}."),
        "es": ("[StepCounter] Alerta: cadencia baja del paciente {patient}",
               "Paciente: {patient}\n\n"
               "La cadencia de marcha fue de {cadence} pasos/min el último día "
               "({cadence_source}), por debajo del umbral de este paciente de "
               "{min_cadence} pasos/min.\n"
               "Último dato recibido: {last_seen}."),
    },
    "SPEED": {
        "en": ("[StepCounter] Alert: patient {patient} low walking speed",
               "Patient: {patient}\n\n"
               "Measured walking speed was {speed} m/s on the latest day, below this "
               "patient's threshold of {min_speed} m/s.\n"
               "Measured over {cycles} gait cycles; stance phase {stance}% of the gait "
               "cycle, cadence {gait_cadence} steps/min.\n"
               "Last data received: {last_seen}.\n\n"
               "Walking speed depends on the device's per-patient stride-length "
               "calibration; check it before acting on an unexpected value."),
        "es": ("[StepCounter] Alerta: velocidad de marcha baja del paciente {patient}",
               "Paciente: {patient}\n\n"
               "La velocidad de marcha medida fue de {speed} m/s el último día, por "
               "debajo del umbral de este paciente de {min_speed} m/s.\n"
               "Medida sobre {cycles} ciclos de marcha; fase de apoyo del {stance} % "
               "del ciclo, cadencia de {gait_cadence} pasos/min.\n"
               "Último dato recibido: {last_seen}.\n\n"
               "La velocidad de marcha depende de la calibración de longitud de zancada "
               "de cada paciente en el dispositivo; compruébela antes de actuar ante un "
               "valor inesperado."),
    },
    "MINUTES": {
        "en": ("[StepCounter] Alert: patient {patient} low active time",
               "Patient: {patient}\n\n"
               "This patient was active for only {active_minutes} minutes on the latest "
               "day, below this patient's threshold of {min_minutes} minutes.\n"
               "Last data received: {last_seen}."),
        "es": ("[StepCounter] Alerta: poco tiempo activo del paciente {patient}",
               "Paciente: {patient}\n\n"
               "Este paciente solo estuvo activo {active_minutes} minutos el último día, "
               "por debajo del umbral de este paciente de {min_minutes} minutos.\n"
               "Último dato recibido: {last_seen}."),
    },
    "LOW": {
        "en": ("[StepCounter] Alert: patient {patient} low activity",
               "Patient: {patient}\n\n"
               "This patient has taken fewer steps than required: {steps} steps on the "
               "latest day, below this patient's threshold of {min_steps} steps.\n"
               "Last data received: {last_seen}."),
        "es": ("[StepCounter] Alerta: actividad baja del paciente {patient}",
               "Paciente: {patient}\n\n"
               "Este paciente ha dado menos pasos de los requeridos: {steps} pasos el "
               "último día, por debajo del umbral de este paciente de {min_steps} "
               "pasos.\n"
               "Último dato recibido: {last_seen}."),
    },
}


def norm_lang(lang):
    return lang if lang in LANGS else DEFAULT_LANG


def t(text, lang, **kw):
    """`text` (English) in `lang`, with {placeholders} filled from kw."""
    if lang == "es":
        text = ES.get(text, text)
    return text.format(**kw) if kw else text


def alert_name(code, lang):
    return ALERT_NAMES[norm_lang(lang)].get(code, code)


def num(value, lang, digits=None):
    """A number as `lang` writes it: 1.5 -> "1,5" in Spanish. Whole floats lose
    their ".0" (a threshold of 80.0 shows as 80). Non-numbers pass through."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return value
    if digits is not None:
        s = f"{value:.{digits}f}"
    elif isinstance(value, float) and value.is_integer():
        s = str(int(value))
    else:
        s = str(value)
    return s.replace(".", ",") if lang == "es" else s


def fmt_ms(ms, lang, day_only=False):
    """An epoch-ms timestamp as local date/time in `lang`'s order."""
    pattern = (DAY_FMT if day_only else DATE_FMT)[norm_lang(lang)]
    return datetime.fromtimestamp(ms / 1000).strftime(pattern)


def email(kind, lang, **fields):
    """(subject, body) of one alert email in `lang`."""
    subject, body = EMAILS[kind][norm_lang(lang)]
    return subject.format(**fields), body.format(**fields)
