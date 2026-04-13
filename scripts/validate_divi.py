"""Script de validacion manual del pipeline Divi con datos reales."""
from src.application.services.builder_detector import BuilderDetector
from src.application.services.divi_extractor import DiviExtractor

divi_content = (
    '[et_pb_section fb_built="1" _builder_version="4.27.4"]'
    '[et_pb_row _builder_version="4.27.4"]'
    '[et_pb_column type="4_4" _builder_version="4.27.4"]'
    '[et_pb_heading title="Conectamos Contigo" _builder_version="4.27.4"][/et_pb_heading]'
    '[et_pb_text _builder_version="4.27.4"]'
    '<p>Tu proxima gran idea merece hacerse realidad. Hablemos de como llevarla al siguiente nivel.</p>'
    '[/et_pb_text]'
    '[/et_pb_column][/et_pb_row][/et_pb_section]'
    '[et_pb_section fb_built="1" _builder_version="4.27.4" global_module="54"]'
    '[et_pb_row _builder_version="4.27.4"]'
    '[et_pb_column type="3_5" _builder_version="4.27.4"]'
    '[et_pb_heading title="No dudes en enviar un Mensaje" _builder_version="4.27.4"][/et_pb_heading]'
    '[et_pb_contact_form email="admin@nyuroframe.expandeya.com" _builder_version="4.27.4"][/et_pb_contact_form]'
    '[/et_pb_column]'
    '[et_pb_column type="2_5" _builder_version="4.27.4"]'
    '[et_pb_heading title="Trabajemos Juntos" _builder_version="4.27.4"][/et_pb_heading]'
    '[et_pb_text _builder_version="4.27.4"]'
    '<p><span>Gracias por su interes en Nyuro Frame. Nos entusiasma saber de usted.</span></p>'
    '[/et_pb_text]'
    '[/et_pb_column][/et_pb_row][/et_pb_section]'
)

detector = BuilderDetector()
report = detector.detect(divi_content)
print("=== DETECCION ===")
print(f"Builder:     {report.builder_type.value}")
print(f"Confianza:   {report.confidence}")
print(f"Modo:        {report.extraction_mode.value}")
print(f"Publicar OK: {report.publish_allowed}")
print(f"Politica:    {report.policy_decision.value}")
print(f"Signals ({len(report.detection_signals)}): {report.detection_signals[:4]}")
print()

extractor = DiviExtractor()
protected = extractor.extract(divi_content)
print("=== EXTRACCION ===")
print(f"Segmentos encontrados: {len(protected.segments)}")
print(f"Tiene global modules:  {protected.has_global_modules}")
for seg in protected.segments:
    print(f"  [{seg.index}] {seg.tag}: {seg.text[:70]}")
