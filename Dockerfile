# Sample build command:
# docker build --rm -t alphalab:dev .

ARG PYTHON_VERSION=3.12
ARG PYTHON_IMG=${PYTHON_VERSION}-slim

# Stage 1: Build dependencies
FROM python:${PYTHON_IMG} AS builder
LABEL org.opencontainers.image.authors="Thanh Nguyen <btnguyen2k (at) gmail(dot)com>"

ARG HOMEDIR=/alphalab
RUN mkdir -p $HOMEDIR
ADD requirements.txt $HOMEDIR
ADD *.py $HOMEDIR
ADD *.md $HOMEDIR
ADD *.env $HOMEDIR
ADD app $HOMEDIR/app
RUN mkdir -p $HOMEDIR/uploads

RUN cd $HOMEDIR \
    && python -m venv .venv \
    && bash -c 'source .venv/bin/activate && pip install -U -r requirements.txt'


# Stage 2: Runtime
FROM python:${PYTHON_IMG} AS runtime
LABEL org.opencontainers.image.authors="Thanh Nguyen <btnguyen2k (at) gmail(dot)com>"

ARG USERNAME=alphalab
ARG USERID=1000
ARG HOMEDIR=/alphalab
RUN useradd --system --create-home --home-dir $HOMEDIR --shell /bin/bash --uid $USERID $USERNAME
COPY --from=builder --chown=$USERNAME $HOMEDIR $HOMEDIR

WORKDIR $HOMEDIR
USER $USERNAME

ENV LISTEN_HOST=0.0.0.0
ENV LISTEN_PORT=8000
ENV NUM_WORKERS=4
EXPOSE 8000

# Prevents Python from writing pyc files to disc (equivalent to python -B option)
ENV PYTHONDONTWRITEBYTECODE=1
# Prevents Python from buffering stdout and stderr (equivalent to python -u option)
ENV PYTHONUNBUFFERED=1
CMD ["bash", "-c", "source ./.venv/bin/activate && python server.py"]
