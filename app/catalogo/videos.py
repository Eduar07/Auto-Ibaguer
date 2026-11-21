"""Los videos de los servicios: cuando el cliente escoge un servicio, el bot le manda el video
de cómo es ese lavado.

El administrador sube el video en Configuración > Menú y precios. WhatsApp no recibe el
archivo en cada mensaje: se le sube una vez, devuelve un id y con ese id se manda. El id
le sirve 30 días; el reloj lo vuelve a subir antes de que se venza (app.reloj.tareas).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session, undefer

from app.catalogo.tablas import Servicio, VideoDelServicio
from app.errores import ReglaDelNegocio

# Lo más pesado que WhatsApp deja mandar en un video.
PESO_MAXIMO = 16 * 1024 * 1024
# WhatsApp recibe MP4 (H.264 con audio AAC). Es lo que sale de casi cualquier celular o editor.
TIPO_MP4 = "video/mp4"
# Meta guarda el archivo 30 días; se vuelve a subir antes, con margen.
VIGENCIA_EN_META = timedelta(days=25)


class VideoInvalido(ReglaDelNegocio):
    """El archivo no sirve como video del servicio. El mensaje dice por qué."""


@dataclass(frozen=True)
class DatosDelVideo:
    """Lo que se muestra del video sin leer el archivo."""

    nombre: str
    peso: int
    subido_en: datetime
    listo_en_whatsapp: bool


def revisar(nombre: str, contenido: bytes) -> None:
    """Que sea un MP4 y que WhatsApp lo acepte por el peso."""
    if not contenido:
        raise VideoInvalido("El archivo está vacío.")
    if len(contenido) > PESO_MAXIMO:
        raise VideoInvalido(
            "El video pesa más de 16 MB y WhatsApp acepta hasta 16 MB. "
            "Usa la versión para WhatsApp."
        )
    # Todo MP4 dice "ftyp" en los bytes 4 a 8; así se sabe aunque el nombre diga otra cosa.
    if contenido[4:8] != b"ftyp":
        raise VideoInvalido(f"«{nombre or 'El archivo'}» no es un video MP4.")


def guardar(
    sesion: Session, servicio_id: int, *, nombre: str, contenido: bytes, ahora: datetime
) -> VideoDelServicio:
    """Pone o cambia el video del servicio. Un video nuevo se sube otra vez a WhatsApp."""
    if sesion.get(Servicio, servicio_id) is None:
        raise VideoInvalido("Ese servicio no existe.")
    nombre = (nombre or "video.mp4").strip()[:200] or "video.mp4"
    revisar(nombre, contenido)
    video = sesion.get(VideoDelServicio, servicio_id)
    if video is None:
        video = VideoDelServicio(servicio_id=servicio_id)
        sesion.add(video)
    video.nombre = nombre
    video.tipo = TIPO_MP4
    video.peso = len(contenido)
    video.contenido = contenido
    video.subido_en = ahora
    video.id_en_meta = None
    video.subido_a_meta_en = None
    sesion.flush()
    return video


def quitar(sesion: Session, servicio_id: int) -> bool:
    """Quita el video del servicio. False si no tenía."""
    borrados = sesion.execute(
        delete(VideoDelServicio).where(VideoDelServicio.servicio_id == servicio_id)
    ).rowcount
    return bool(borrados)


def del_servicio(
    sesion: Session, servicio_id: int, *, con_archivo: bool = False
) -> VideoDelServicio | None:
    consulta = select(VideoDelServicio).where(VideoDelServicio.servicio_id == servicio_id)
    if con_archivo:
        consulta = consulta.options(undefer(VideoDelServicio.contenido))
    return sesion.scalars(consulta).first()


def tiene_video(sesion: Session, servicio_id: int) -> bool:
    return (
        sesion.scalar(
            select(VideoDelServicio.servicio_id).where(VideoDelServicio.servicio_id == servicio_id)
        )
        is not None
    )


def id_vigente(video: VideoDelServicio, ahora: datetime) -> str | None:
    """El id con que WhatsApp tiene el video, si todavía sirve."""
    subido = video.subido_a_meta_en
    if video.id_en_meta and subido is not None and ahora - subido < VIGENCIA_EN_META:
        return video.id_en_meta
    return None


def anotar_subida(video: VideoDelServicio, id_en_meta: str, ahora: datetime) -> None:
    video.id_en_meta = id_en_meta
    video.subido_a_meta_en = ahora


def datos_de_los_videos(sesion: Session, ahora: datetime) -> dict[int, DatosDelVideo]:
    """Los videos de todos los servicios (sin leer los archivos), para Configuración."""
    return {
        video.servicio_id: DatosDelVideo(
            nombre=video.nombre,
            peso=video.peso,
            subido_en=video.subido_en,
            listo_en_whatsapp=id_vigente(video, ahora) is not None,
        )
        for video in sesion.scalars(select(VideoDelServicio))
    }


def por_subir_a_whatsapp(sesion: Session, ahora: datetime) -> list[int]:
    """Los servicios cuyo video WhatsApp no tiene o tiene por vencerse."""
    return list(
        sesion.scalars(
            select(VideoDelServicio.servicio_id)
            .where(
                or_(
                    VideoDelServicio.id_en_meta.is_(None),
                    VideoDelServicio.subido_a_meta_en.is_(None),
                    VideoDelServicio.subido_a_meta_en <= ahora - VIGENCIA_EN_META,
                )
            )
            .order_by(VideoDelServicio.servicio_id)
        )
    )


def para_subir(sesion: Session, servicio_id: int) -> VideoDelServicio | None:
    """El video con su archivo, bloqueado mientras se sube: si dos relojes corren a la vez,
    el segundo se lo salta y no lo sube dos veces."""
    return sesion.scalars(
        select(VideoDelServicio)
        .where(VideoDelServicio.servicio_id == servicio_id)
        .options(undefer(VideoDelServicio.contenido))
        .with_for_update(skip_locked=True)
    ).first()


def peso_para_mostrar(peso: int) -> str:
    """12345678 -> "11,8 MB"."""
    return f"{peso / (1024 * 1024):.1f} MB".replace(".", ",")
