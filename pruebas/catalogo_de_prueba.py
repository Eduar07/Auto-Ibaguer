"""El menú con el que corren las pruebas.

Es más largo que el del negocio (cinco servicios de carro y tres de moto) a propósito: así
se prueba lo que pasa con listas largas, varios precios y servicios que el negocio todavía
no ofrece. El menú de verdad está en app.catalogo.datos_iniciales y tiene su propia prueba
(test_tablas_y_datos).
"""

DURACION_DE_PRUEBA = 60

SERVICIOS_DE_PRUEBA = (
    (
        "Carros",
        "Básico",
        "Lavado exterior a presión, champú, secado y brillo de llantas",
        {"Automóvil": 40_000, "Camioneta": 50_000},
    ),
    (
        "Carros",
        "Encerado",
        "Básico + cera protectora para la pintura",
        {"Automóvil": 60_000, "Camioneta": 70_000},
    ),
    (
        "Carros",
        "General",
        "Encerado + aspirado completo, tablero, puertas y vidrios por dentro",
        {"Automóvil": 120_000, "Camioneta": 140_000},
    ),
    (
        "Carros",
        "Premium",
        "General + limpieza de cojinería, lavado de motor e hidratación de plásticos",
        {"Automóvil": 160_000, "Camioneta": 220_000},
    ),
    (
        "Carros",
        "Empelotado",
        "Desmonte de sillas y alfombras, cojinería, techo, chasis, motor, encerado y pulido",
        {"Automóvil": 300_000, "Camioneta": 400_000},
    ),
    ("Motos", "Básico", "Lavado a presión, champú y secado", {"Moto": 18_000}),
    (
        "Motos",
        "Intermedio",
        "Básico + desengrase de motor y cadena, brillo de llantas",
        {"Moto": 25_000},
    ),
    (
        "Motos",
        "Premium",
        "Intermedio + lubricación de cadena, brillo de plásticos y cromados, protector",
        {"Moto": 35_000},
    ),
)
