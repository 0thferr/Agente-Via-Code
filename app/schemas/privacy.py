from typing import Literal

from pydantic import BaseModel, Field

DELETION_CONFIRMATION = "EXCLUIR MINHA CONTA"


class AccountDeletionRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128)
    confirmation: Literal["EXCLUIR MINHA CONTA"] = Field(
        description=f'Digite exatamente "{DELETION_CONFIRMATION}".'
    )
