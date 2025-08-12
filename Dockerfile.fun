```Dockerfile
# Dockerfile for the OCI Function
FROM fnproject/python:3.8-dev as build-stage
WORKDIR /function
ADD vision_function/requirements.txt /function/
RUN pip install --target /python/  --no-cache-dir -r requirements.txt &&\
    rm -fr ~/.cache/pip /tmp* requirements.txt
ADD vision_function/ /function/
RUN rm -fr /function/.pip_cache

FROM fnproject/python:3.8
WORKDIR /function
COPY --from=build-stage /python /python
COPY --from=build-stage /function /function
ENTRYPOINT ["/python/bin/fdk", "/function/func.py", "handler"]
```
