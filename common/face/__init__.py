"""Face pipeline primitives: detect -> quality gates -> align -> embed -> match.

Ported from InsightFace ``buffalo_s`` pre/post-processing so the Pi can run the
ONNX models through ``onnxruntime`` directly. Filled in during milestone M3.
"""
