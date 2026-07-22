from pydantic import BaseModel, Field


class MonitorPayload(BaseModel):
    temperature: float = Field(default=..., description="Nhiệt độ (°C)")
    humidity: float = Field(default=..., description="Độ ẩm (%)")
    smoke: int = Field(default=..., description="Chỉ số khói MQ-2")
    alarm: bool = Field(default=..., description="Cảnh báo từ firmware")


class SystemStatusResponse(BaseModel):
    status: str
    temp: float
    smoke: int
    current_dynamic_threshold: int
    timestamp: str
