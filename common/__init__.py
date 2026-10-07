"""Code shared by the device, the server and the tools.

Everything in here must stay pure Python + numpy + onnxruntime so that it runs
unchanged on the Raspberry Pi (no ``insightface`` package, no heavy frameworks).
"""
