"""
fixtures/page_content_fixtures.py — Fixtures HTML/shortcode realistas por builder.

Todos los fixtures son representativos del contenido real que
la REST API de WordPress retorna en content.raw para cada builder.
"""

# ── Gutenberg ─────────────────────────────────────────────────────────────────
GUTENBERG_PAGE = """\
<!-- wp:heading {"level":2} -->
<h2 class="wp-block-heading">Nuestros Servicios de Neurología</h2>
<!-- /wp:heading -->

<!-- wp:paragraph -->
<p>Ofrecemos diagnóstico y tratamiento integral de trastornos neurológicos con tecnología de última generación.</p>
<!-- /wp:paragraph -->

<!-- wp:list -->
<ul class="wp-block-list">
<!-- wp:list-item -->
<li>Electroencefalografía digital</li>
<!-- /wp:list-item -->
<!-- wp:list-item -->
<li>Resonancia magnética funcional</li>
<!-- /wp:list-item -->
</ul>
<!-- /wp:list -->
"""

# ── Classic Editor ────────────────────────────────────────────────────────────
CLASSIC_PAGE = """\
<h2>Sobre Nosotros</h2>
<p>Somos una clínica especializada en neurología con más de 15 años de experiencia en la región.</p>
<p>Nuestro equipo de especialistas está comprometido con ofrecer la mejor atención a cada paciente.</p>
<ul>
<li>Atención personalizada las 24 horas</li>
<li>Equipos de diagnóstico de última generación</li>
</ul>
"""

# ── Divi ──────────────────────────────────────────────────────────────────────
DIVI_PAGE = """\
[et_pb_section fb_built="1" _builder_version="4.27.4" _module_preset="default"][et_pb_row _builder_version="4.27.4" _module_preset="default"][et_pb_column type="4_4" _builder_version="4.27.4" _module_preset="default"][et_pb_heading title="Conectamos Contigo" _builder_version="4.27.4" _module_preset="default"][/et_pb_heading][et_pb_text _builder_version="4.27.4" _module_preset="default"]<p>Tu próxima gran idea merece hacerse realidad. Hablemos de cómo llevarla al siguiente nivel.</p>[/et_pb_text][et_pb_button button_text="Contáctanos" button_url="#contacto" _builder_version="4.27.4" _module_preset="default"][/et_pb_button][/et_pb_column][/et_pb_row][/et_pb_section]
[et_pb_section fb_built="1" _builder_version="4.27.4" _module_preset="default"][et_pb_row _builder_version="4.27.4" _module_preset="default"][et_pb_column type="4_4" _builder_version="4.27.4" _module_preset="default"][et_pb_accordion _builder_version="4.27.4"][et_pb_accordion_item title="¿Cuánto tiempo tarda el proyecto?" open="on" _builder_version="4.27.4"]<p>El desarrollo típico toma entre una y dos semanas, dependiendo de la complejidad del sitio solicitado.</p>[/et_pb_accordion_item][et_pb_accordion_item title="¿Ofrecen soporte técnico?" _builder_version="4.27.4"]<p>Sí ofrecemos soporte técnico continuo durante los primeros seis meses tras la entrega del proyecto.</p>[/et_pb_accordion_item][/et_pb_accordion][/et_pb_column][/et_pb_row][/et_pb_section]
"""

# Divi con Global Module (requiere warning especial)
DIVI_PAGE_WITH_GLOBAL_MODULE = """\
[et_pb_section fb_built="1" _builder_version="4.27.4" global_module="53" saved_tabs="all"][et_pb_row _builder_version="4.27.4"][et_pb_column type="4_4" _builder_version="4.27.4"][et_pb_heading title="Header Global de Todas las Páginas" _builder_version="4.27.4"][/et_pb_heading][/et_pb_column][/et_pb_row][/et_pb_section]
[et_pb_section fb_built="1" _builder_version="4.27.4"][et_pb_row _builder_version="4.27.4"][et_pb_column type="4_4" _builder_version="4.27.4"][et_pb_text _builder_version="4.27.4"]<p>Contenido local de esta página específica sobre nuestros servicios de neurología.</p>[/et_pb_text][/et_pb_column][/et_pb_row][/et_pb_section]
"""

# ── Elementor (HTML renderizado) ──────────────────────────────────────────────
ELEMENTOR_RAW_CONTENT = """\
<!-- Elementor:{"id":"abc123","version":"3.24.0"} -->
"""

ELEMENTOR_RENDERED_HTML = """\
<!DOCTYPE html>
<html>
<head><title>Servicios - NyuroFrame</title></head>
<body>
<div data-elementor-type="wp-page" data-elementor-id="42" class="elementor elementor-42">
  <section class="elementor-section elementor-top-section">
    <div class="elementor-container">
      <div class="elementor-column elementor-col-100">
        <div class="elementor-widget-container">
          <h2 class="elementor-heading-title">Diagnóstico Neurológico Avanzado</h2>
        </div>
        <div class="elementor-widget-container">
          <p>Utilizamos tecnología de punta para diagnosticar y tratar trastornos neurológicos complejos con precisión.</p>
        </div>
        <div class="elementor-widget-container">
          <ul>
            <li>Resonancia magnética de alta resolución</li>
            <li>Electroencefalografía ambulatoria</li>
            <li>Estimulación magnética transcraneal</li>
          </ul>
        </div>
      </div>
    </div>
  </section>
</div>
</body>
</html>
"""

# ── Oxygen ────────────────────────────────────────────────────────────────────
OXYGEN_RAW_CONTENT = """\
[ct_section id="1"][ct_div_block id="2"][ct_text_block id="3"]Sobre nosotros[/ct_text_block][/ct_div_block][/ct_section]
"""

OXYGEN_RENDERED_HTML = """\
<!DOCTYPE html>
<html>
<body class="oxy-page">
  <div class="oxy-section">
    <div class="oxy-div-block">
      <h1>Clínica de Neurología Avanzada</h1>
      <p>Especialistas en diagnóstico y tratamiento de enfermedades neurológicas con más de diez años de experiencia.</p>
    </div>
  </div>
</body>
</html>
"""

# ── Breakdance ────────────────────────────────────────────────────────────────
BREAKDANCE_RENDERED_HTML = """\
<!DOCTYPE html>
<html>
<body>
  <div class="bde-section">
    <div class="bde-container">
      <h2 class="bde-heading">Nuestros Tratamientos Neurológicos</h2>
      <p class="bde-text">Ofrecemos tratamientos especializados para una amplia gama de condiciones neurológicas.</p>
    </div>
  </div>
</body>
</html>
"""

# ── Bricks ────────────────────────────────────────────────────────────────────
BRICKS_RENDERED_HTML = """\
<!DOCTYPE html>
<html>
<body>
  <div class="brxe-container">
    <div class="brxe-block">
      <h2 class="brxe-heading">Centro de Excelencia Neurológica</h2>
      <p class="brxe-text">Somos referentes en el tratamiento de patologías neurológicas complejas en toda la región.</p>
    </div>
  </div>
</body>
</html>
"""

# ── Página sin contenido útil ─────────────────────────────────────────────────
EMPTY_PAGE = """\
[et_pb_section fb_built="1" _builder_version="4.27.4"][et_pb_row _builder_version="4.27.4"][et_pb_column type="4_4" _builder_version="4.27.4"][et_pb_image src="https://ejemplo.com/imagen.jpg" _builder_version="4.27.4"][/et_pb_image][/et_pb_column][/et_pb_row][/et_pb_section]
"""

# ── Página con shortcodes simples (Classic + shortcodes) ──────────────────────
CLASSIC_WITH_SHORTCODES = """\
<h2>Formulario de Contacto</h2>
<p>Completa el formulario a continuación y nos pondremos en contacto contigo a la brevedad posible.</p>
[contact-form-7 id="123" title="Contacto"]
<p>También puedes llamarnos al número que aparece en la parte superior de nuestra web.</p>
"""
