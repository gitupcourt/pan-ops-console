from pydantic import BaseModel


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: int
    username: str
    email: str | None
    is_admin: bool
    is_active: bool
    auth_provider: str

    class Config:
        from_attributes = True
