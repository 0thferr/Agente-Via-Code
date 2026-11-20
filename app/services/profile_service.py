import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import NotFoundError
from app.models.profile import (
    Certification,
    Education,
    Language,
    ProfessionalProfile,
    Skill,
    WorkExperience,
)
from app.schemas.profile import ProfileIn
from app.services.audit_service import record_audit_event

NESTED_FIELDS = {"experiences", "educations", "skills", "languages", "certifications"}


def get_profile(session: Session, user_id: uuid.UUID) -> ProfessionalProfile:
    profile = session.scalar(
        select(ProfessionalProfile)
        .where(ProfessionalProfile.user_id == user_id)
        .options(*(selectinload(getattr(ProfessionalProfile, field)) for field in NESTED_FIELDS))
    )
    if profile is None:
        raise NotFoundError("Perfil profissional ainda não cadastrado.")
    return profile


def list_skills(session: Session, user_id: uuid.UUID) -> list[Skill]:
    return list(
        session.scalars(
            select(Skill)
            .join(ProfessionalProfile, Skill.profile_id == ProfessionalProfile.id)
            .where(ProfessionalProfile.user_id == user_id)
            .order_by(Skill.category, Skill.name)
        )
    )


def save_profile(
    session: Session, user_id: uuid.UUID, data: ProfileIn, *, source: str = "manual"
) -> ProfessionalProfile:
    """Cria ou substitui por completo o perfil do usuário em uma única transação."""
    try:
        profile = get_profile(session, user_id)
        created = False
    except NotFoundError:
        profile = ProfessionalProfile(user_id=user_id)
        session.add(profile)
        created = True

    for field, value in data.model_dump(exclude=NESTED_FIELDS).items():
        setattr(profile, field, value)

    # Remove os itens antigos antes de inserir os novos: o SQLAlchemy executa INSERTs
    # antes de DELETEs no flush, o que violaria as constraints de unicidade.
    for field in NESTED_FIELDS:
        setattr(profile, field, [])
    session.flush()

    profile.experiences = [WorkExperience(**item.model_dump()) for item in data.experiences]
    profile.educations = [Education(**item.model_dump()) for item in data.educations]
    profile.skills = [Skill(**item.model_dump()) for item in data.skills]
    profile.languages = [Language(**item.model_dump()) for item in data.languages]
    profile.certifications = [Certification(**item.model_dump()) for item in data.certifications]

    session.flush()
    record_audit_event(
        session,
        action="profile.created" if created else "profile.updated",
        user_id=user_id,
        entity_type="professional_profile",
        entity_id=profile.id,
        details={"source": source},
    )
    session.commit()
    return get_profile(session, user_id)
