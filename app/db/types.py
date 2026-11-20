from enum import Enum as PyEnum

from sqlalchemy import Enum


def text_enum(enum_class: type[PyEnum]) -> Enum:
    """Enum salvo como texto (VARCHAR + CHECK), não como tipo nativo do PostgreSQL.

    Adicionar um valor novo exige só uma migration simples, sem ALTER TYPE.
    """
    return Enum(
        enum_class,
        native_enum=False,
        length=32,
        values_callable=lambda members: [member.value for member in members],
    )
