from pydantic import BaseModel


class StreamTicketResponse(BaseModel):
    ticket: str
    expires_in: int
