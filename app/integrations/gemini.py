"""Gemini API for interpreting operator requests and invoking scheduling tools."""


class GeminiSchedulingAssistant:
    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    def interpret_reallocation_request(self, instruction: str, context: dict) -> dict:
        raise NotImplementedError(
            "Gemini interpretation is not implemented. Wire API key and model via config."
        )

    def call_scheduling_tool(self, tool_name: str, arguments: dict) -> dict:
        raise NotImplementedError("Gemini tool-calling for scheduling is not implemented.")