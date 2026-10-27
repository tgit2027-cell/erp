from rest_framework.renderers import JSONRenderer

from .load_diagnostics import measure_stage


class LoadMeasuredJSONRenderer(JSONRenderer):
    """Default JSON output with timing collected only inside the benchmark context."""

    def render(self, data, accepted_media_type=None, renderer_context=None):
        with measure_stage("json_render"):
            return super().render(data, accepted_media_type, renderer_context)
