"""
Exporta as amostras gravadas durante o treino para .tcx (Training Center
XML) — formato aceito nativamente por upload manual no Garmin Connect e no
TrainingPeaks, preservando FC, potência, cadência e a estrutura de laps
(cada lap = uma etapa do treino).

Potência não faz parte do schema TCX v2 padrão; é transmitida via extensão
oficial da Garmin (ActivityExtension/v2, elemento TPX/Watts), que é o
mecanismo que TrainingPeaks e Garmin Connect já sabem ler.
"""
from datetime import datetime, timezone
from xml.etree import ElementTree as ET
from xml.dom import minidom

TCX_NS = "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"
ACT_EXT_NS = "http://www.garmin.com/xmlschemas/ActivityExtension/v2"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"

ET.register_namespace("", TCX_NS)
ET.register_namespace("ext", ACT_EXT_NS)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _group_by_lap(samples: list[dict]) -> list[list[dict]]:
    """Agrupa amostras contíguas pelo step_index, preservando a ordem."""
    laps: list[list[dict]] = []
    current_idx = None
    current_group: list[dict] = []

    for s in samples:
        if s["step_index"] != current_idx:
            if current_group:
                laps.append(current_group)
            current_group = []
            current_idx = s["step_index"]
        current_group.append(s)

    if current_group:
        laps.append(current_group)

    return laps


def export_tcx(samples: list[dict], sport: str = "Biking") -> str:
    """Recebe a lista de amostras (state.samples) e devolve a string XML do .tcx."""
    if not samples:
        raise ValueError("Nenhuma amostra gravada — treino não foi iniciado ou terminou sem dados.")

    root = ET.Element(f"{{{TCX_NS}}}TrainingCenterDatabase")
    root.set(f"{{{XSI_NS}}}schemaLocation",
             f"{TCX_NS} https://www8.garmin.com/xmlschemas/TrainingCenterDatabasev2.xsd")

    activities = ET.SubElement(root, f"{{{TCX_NS}}}Activities")
    activity = ET.SubElement(activities, f"{{{TCX_NS}}}Activity")
    activity.set("Sport", sport)

    activity_id = ET.SubElement(activity, f"{{{TCX_NS}}}Id")
    activity_id.text = _iso(samples[0]["wall_time"])

    laps = _group_by_lap(samples)

    for lap_samples in laps:
        lap_start = lap_samples[0]["wall_time"]
        lap_end = lap_samples[-1]["wall_time"]
        lap_duration = max(1.0, lap_end - lap_start)

        hr_values = [s["heart_rate"] for s in lap_samples if s["heart_rate"]]

        lap_el = ET.SubElement(activity, f"{{{TCX_NS}}}Lap")
        lap_el.set("StartTime", _iso(lap_start))

        ET.SubElement(lap_el, f"{{{TCX_NS}}}TotalTimeSeconds").text = f"{lap_duration:.1f}"
        ET.SubElement(lap_el, f"{{{TCX_NS}}}DistanceMeters").text = "0.0"
        ET.SubElement(lap_el, f"{{{TCX_NS}}}Calories").text = "0"

        if hr_values:
            avg_hr_el = ET.SubElement(lap_el, f"{{{TCX_NS}}}AverageHeartRateBpm")
            ET.SubElement(avg_hr_el, f"{{{TCX_NS}}}Value").text = str(round(sum(hr_values) / len(hr_values)))
            max_hr_el = ET.SubElement(lap_el, f"{{{TCX_NS}}}MaximumHeartRateBpm")
            ET.SubElement(max_hr_el, f"{{{TCX_NS}}}Value").text = str(max(hr_values))

        ET.SubElement(lap_el, f"{{{TCX_NS}}}Intensity").text = (
            "Resting" if lap_samples[0]["step_name"] and "recup" in lap_samples[0]["step_name"].lower()
            else "Active"
        )
        ET.SubElement(lap_el, f"{{{TCX_NS}}}TriggerMethod").text = "Manual"

        # nome da etapa não tem campo padrão no TCX — anexado como Notes
        if lap_samples[0]["step_name"]:
            ET.SubElement(lap_el, f"{{{TCX_NS}}}Notes").text = lap_samples[0]["step_name"]

        track_el = ET.SubElement(lap_el, f"{{{TCX_NS}}}Track")

        for s in lap_samples:
            tp = ET.SubElement(track_el, f"{{{TCX_NS}}}Trackpoint")
            ET.SubElement(tp, f"{{{TCX_NS}}}Time").text = _iso(s["wall_time"])

            if s["heart_rate"]:
                hr_el = ET.SubElement(tp, f"{{{TCX_NS}}}HeartRateBpm")
                ET.SubElement(hr_el, f"{{{TCX_NS}}}Value").text = str(s["heart_rate"])

            if s["cadence"]:
                ET.SubElement(tp, f"{{{TCX_NS}}}Cadence").text = str(s["cadence"])

            if s["power"]:
                ext = ET.SubElement(tp, f"{{{TCX_NS}}}Extensions")
                tpx = ET.SubElement(ext, f"{{{ACT_EXT_NS}}}TPX")
                ET.SubElement(tpx, f"{{{ACT_EXT_NS}}}Watts").text = str(s["power"])

    rough_string = ET.tostring(root, encoding="utf-8")
    return minidom.parseString(rough_string).toprettyxml(indent="  ")


def export_tcx_to_file(samples: list[dict], path: str, sport: str = "Biking"):
    xml_str = export_tcx(samples, sport=sport)
    with open(path, "w", encoding="utf-8") as f:
        f.write(xml_str)
