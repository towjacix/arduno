from pydantic import BaseModel, Field


class MonitorPayload(BaseModel):
    temp: float = Field(default=..., description="Nhiệt độ (°C)")
    smoke: int = Field(default=..., description="Chỉ số khói MQ-2")


class SystemStatusResponse(BaseModel):
    status: str
    temp: float
    smoke: int
    current_dynamic_threshold: int
    timestamp: str
