FROM python:3.7.6-slim-buster AS builder
ADD . /src
WORKDIR /wheels
RUN pip wheel /src
WORKDIR /src
RUN python setup.py bdist_wheel

FROM python:3.7.6-slim-buster
COPY --from=builder /src/dist /dist
COPY --from=builder /wheels /wheels
RUN pip install /dist/*.whl -f /wheels
RUN rm -rf /dist /wheels
CMD kopf run -m secret_sync.handlers --verbose
