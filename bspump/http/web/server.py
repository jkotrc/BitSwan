import logging
import asyncio
import json
import jwt
from jwt.exceptions import ExpiredSignatureError, DecodeError
from typing import Callable
import base64
from io import BytesIO
from jinja2 import Environment, FileSystemLoader

from ...abc.source import Source
from ...abc.sink import Sink
from ...abc.connection import Connection

import aiohttp.web
from aiohttp.web import Request
from importlib.resources import files


L = logging.getLogger(__name__)
env = Environment(loader=FileSystemLoader("bspump/http/web/templates"))


def recursive_merge(dict1, dict2):
    for key, value in dict2.items():
        if key in dict1 and isinstance(dict1[key], dict) and isinstance(value, dict):
            dict1[key] = recursive_merge(dict1[key], value)
        else:
            dict1[key] = value
    return dict1


class WebServerConnection(Connection):
    """
    Source with events from a specific route.
    """

    ConfigDefaults = {
        "port": 8080,
        "max_body_size_bytes": 1024 * 1024 * 1000,
    }

    def __init__(self, app, id=None, config=None):
        super().__init__(app, id=id, config=config)

        self.aiohttp_app = aiohttp.web.Application(
            client_max_size=int(self.Config["max_body_size_bytes"])
        )
        static_dir = str(files("bspump").joinpath("static/css"))
        print(static_dir)
        self.aiohttp_app.router.add_static("/static/", static_dir, show_index=True)
        self.start_server()

    def start_server(self):
        print("Starting webserver")
        try:
            self.App.Loop.create_task(
                aiohttp.web._run_app(
                    self.aiohttp_app,
                    port=int(self.Config["port"]),
                )
            )
        except Exception as e:
            print("Exception: {}".format(e))
            import traceback

            traceback.print_exc()


class WebRouteSource(Source):
    """
    WebSource is a source that listens on a specified port and serves HTTP requests.
    """

    def __init__(
        self,
        app,
        pipeline,
        connection="DefaultWebServerConnection",
        method="GET",
        route="/",
        id=None,
        config=None,
    ):
        super().__init__(app, pipeline, id=id, config=config)
        pipeline.StopOnErrors = False

        try:
            self.Connection = pipeline.locate_connection(app, connection)
        except KeyError:
            if connection == "DefaultWebServerConnection":
                self.Connection = WebServerConnection(app, "DefaultWebServerConnection")
                app.PumpService.add_connection(self.Connection)
        self.aiohttp_app = self.Connection.aiohttp_app
        self.aiohttp_app.router.add_route(method, route, self.handle_request)

    async def main(self):
        pass

    async def handle_request(self, request):
        try:
            response_future = asyncio.Future()
            await self.process(
                {
                    "request": request,
                    "response_future": response_future,
                    "status": 200,
                }
            )
            return await response_future
        except Exception:
            L.exception("Exception in WebSource")
            return aiohttp.web.Response(status=500)


async def gate_response(request, test_secret, response_fn):
    secret = request.query.get("secret")
    if secret is None:
        auth = request.headers.get("Authorization")
        if auth is not None:
            if auth.startswith("Bearer "):
                secret = auth[7:]
        if secret is None:
            return aiohttp.web.Response(
                text="Secret is missing. Pass via query parameter 'secret' or in the Authorization as 'Bearer <secret>'",
                status=401,
            )
    if not test_secret(secret):
        return aiohttp.web.Response(text="Invalid secret", status=403)
    return await response_fn()


class ProtectedWebRouteSource(WebRouteSource):
    """
    Web route source that requires a secret in a qparam or in the BearerToken.
    """

    async def handle_request(self, request: Request):
        try:

            async def response_fn():
                response_future = asyncio.Future()
                await self.process(
                    {
                        "request": request,
                        "response_future": response_future,
                        "status": 200,
                    }
                )
                return await response_future

            return await gate_response(request, self.Config["secret"], response_fn)
        except Exception:
            L.exception("Exception in WebSource")
            return aiohttp.web.Response(status=500)


class BaseField:
    def __init__(self, name, **kwargs):
        self.name = name
        self.hidden: bool = kwargs.get("hidden", False)
        self.required: bool = kwargs.get("required", True)
        self.display: str = kwargs.get("display", self.name)
        self.description: str = kwargs.get("description", "")
        self.default = kwargs.get("default", "")

    def html(self, defaults) -> str:
        pass

    def get_params(self, defaults) -> dict:
        pass

    def restructure_data(self, dfrom, dto):
        pass

    def clean(self, data, request: Request = None):
        pass


class FieldSet(BaseField):
    def __init__(self, name, fields=None, fieldset_intro="", display="", required=True, **kwargs):
        super().__init__(name, **kwargs)
        su = super()
        self.fields = fields
        if fields is None:
            self.fields = []
        su.__init__(name, required=required)
        self.display = display if display else name
        self.default = {}
        self.fieldset_intro = fieldset_intro
        self.prefix = f"fieldset___{self.name}___"

    def set_subfield_names(self):
        for field in self.fields:
            field.field_name = f"{self.prefix}{field.name}"

    def html(self, defaults={}):
        self.set_subfield_names()
        self.set_subfield_names()
        fields_html = [field.html(defaults.get(field.name, field.default)) for field in self.fields]
        template = env.get_template("fieldset.html")
        return template.render(display=self.display, fieldset_intro=self.fieldset_intro, fields=fields_html)


    def get_params(self, defaults) -> dict:
            params = {}
            for field in self.fields:
                params[field.name] = field.get_params(defaults.get(field.name, ""))
            return params

    def restructure_data(self, dfrom, dto):
        self.set_subfield_names()
        dto[self.name] = {}
        for field in self.fields:
            field.restructure_data(dfrom, dto[self.name])

    def clean(self, data, request: Request = None):
        for field in self.fields:
            field.clean(data[self.name])


class Field(BaseField):
    def __init__(self, name, **kwargs):
        super().__init__(name, **kwargs)
        if "___" in name:
            raise ValueError("Field name cannot contain '___'")
        su = super()
        su.__init__(name, **kwargs)
        self.readonly: bool = kwargs.get("readonly", False)
        self.default = kwargs.get("default", "")
        self.field_name: str = f"f___{self.name}"
        self.default_classes = kwargs.get(
            "default_css_classes",
            "bg-gray-50 border border-gray-300 text-gray-900 text-sm rounded-lg focus:ring-blue-500 focus:border-blue-500 block w-full p-2.5 dark:bg-gray-700 dark:border-gray-600 dark:placeholder-gray-400 dark:text-white dark:focus:ring-blue-500 dark:focus:border-blue-500",
        )
        if self.readonly:
            self.default_classes = kwargs.get(
                "default_css_classes",
                "bg-gray-500 border border-gray-300 text-white text-sm rounded-lg focus:ring-blue-500 focus:border-blue-500 block w-full p-2.5 dark:bg-gray-700 dark:border-gray-600 dark:placeholder-gray-400 dark:text-white dark:focus:ring-blue-500 dark:focus:border-blue-500",
            )

    @property
    def default_input_props(self):
        if self.readonly:
            readonly = "readonly"
        else:
            readonly = ""

        if self.required:
            required = 'required aria-required="true"'
        else:
            required = ""
        return f'name="{self.field_name}" id="{self.field_name}" {readonly} {required}'

    def restructure_data(self, dfrom, dto):
        dto[self.name] = dfrom.get(self.field_name, self.default)

    def clean(self, data, request: Request | None = None):
        pass

    def html(self, default=""):
        if not default:
            default = self.default

        template = env.get_template("field.html")
        return template.render(
            field_name=self.field_name,
            display=self.display,
            default_classes=self.default_classes,
            hidden=self.hidden,
            inner_html=self.inner_html(default, self.readonly),
        )

    def get_params(self, default="") -> dict:
        return {self.name: {"type": str(type(self)), "description": self.description}}


class TextField(Field):
    def inner_html(self, default="", readonly=False):
        template = env.get_template("text-field.html")
        return template.render(
            default=default,
            default_classes=self.default_classes,
            default_input_props=self.default_input_props,
        )


class ChoiceField(Field):
    def __init__(self, name, choices, **kwargs):
        super().__init__(name, **kwargs)
        self.choices = choices

    def inner_html(self, default="", readonly=False):
        template = env.get_template("choice-field.html")
        return template.render(
            default=default,
            choices=self.choices,
            default_classes=self.default_classes,
            default_input_props=self.default_input_props,
        )


class CheckboxField(Field):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default = self.default or False

    def inner_html(self, default="", readonly=False):
        readonly_attr = "disabled" if readonly else ""

        template = env.get_template("checkbox-field.html")
        return template.render(
            default=default,
            readonly=readonly,
            default_classes=self.default_classes,
            default_input_props=self.default_input_props,
            readonly_attr=readonly_attr,
        )

    def clean(self, data, request: Request | None = None):
        if type(data.get(self.name)) == str:
            data[self.name] = data.get(self.name, False) == "on"


class IntField(Field):
    def inner_html(self, default=0, readonly=False):
        if not default:
            default = 0

        template = env.get_template("number-field.html")
        return template.render(
            default=default,
            default_input_props=self.default_input_props,
            default_classes=self.default_classes
        )

    def clean(self, data, request: Request | None = None):
        if type(data.get(self.name)) == str:
            data[self.name] = int(data.get(self.name, 0))


class FloatField(Field):
    def inner_html(self, default=0, readonly=False):
        if not default:
            default = 0.0

        template = env.get_template("number-field.html")
        return template.render(
            default=default,
            default_input_props=self.default_input_props,
            default_classes=self.default_classes
        )

    def clean(self, data, request: Request | None = None):
        if type(data.get(self.name)) == str:
            data[self.name] = float(data.get(self.name, 0))


class FileField(Field):
    """
    The value ends up being the bytes of the uploaded file.
    """

    def inner_html(self, default="", readonly=False):
        template = env.get_template("file-field.html")
        return template.render(
            default_input_props=self.default_input_props,
            default_classes=self.default_classes,
        )

    def clean(self, data, request: Request | None = None):
        if request.content_type == "application/json":
            decoded_data = base64.b64decode(data.get(self.name, ""))
            data[self.name] = BytesIO(decoded_data)
        else:
            # in case of not submitting any file
            if data[self.name] == b"":
                data[self.name] = BytesIO(b"")
            else:
                data[self.name] = data[self.name].file


class RawJSONField(Field):
    def inner_html(self, default="", readonly=False):
        template = env.get_template("raw-json_field.html")
        return template.render(
            default=default,
            default_input_props=self.default_input_props,
            default_classes=self.default_classes,
        )

    def clean(self, data, request: Request | None = None):
        if type(data.get(self.name)) == str:
            data[self.name] = json.loads(data.get(self.name, "{}"))


class WebFormSource(WebRouteSource):
    def __init__(
        self,
        app,
        pipeline,
        connection="DefaultWebServerConnection",
        route="/",
        fields: list[BaseField] | Callable[[Request], list[BaseField]] = lambda r: [],
        id=None,
        config=None,
        form_intro="",
    ):
        super().__init__(
            app,
            pipeline,
            connection=connection,
            route=route,
            method="GET",
            id=id,
            config=config,
        )
        self.fields = []
        self.generate_fields = None
        if isinstance(fields, list):
            self.fields = fields
        elif isinstance(fields, Callable):
            self.generate_fields = fields
        else:
            raise ValueError(
                f"incorrect type {type(fields)}. Expected list[Field] or Callable[[Request] -> list[Field]]"
            )

        self.form_intro = form_intro
        self.aiohttp_app.router.add_route("POST", route, self.handle_post)

    async def handle_request(self, request: Request):
        if self.generate_fields:
            self.fields = self.generate_fields(request)
        if request.content_type == "application/json":
            defaults = self.extract_defaults(request)
            field_info = {f.name: f.get_params(defaults) for f in self.fields}
            return aiohttp.web.json_response(field_info)
        return aiohttp.web.Response(
            text=self.render_form(request),
            content_type="text/html",
        )

    def load_fields(self, request: Request):
        if self.generate_fields:
            self.fields = self.generate_fields(request)

    def validate_field_presence(self, data, fields=None):
        if not fields:
            fields = self.fields
        for field in fields:
            if field.required and field.name not in data:
                raise ValueError(f"Field {field.name} is required")
            if field.required and hasattr(field, "fields"):
                self.validate_field_presence(data[field.name], field.fields)

    async def extract_data(self, request: Request):
        if request.content_type == "application/json":
            data = await request.json()
        else:
            dfrom = dict(await request.post())
            data = {}
            for field in self.fields:
                field.restructure_data(dfrom, data)
        return data

    async def clean_and_process(self, request: Request, data):
        for field in self.fields:
            try:
                field.clean(data, request=request)
            except ValueError as e:
                return aiohttp.web.Response(
                    text=self.render_form(request, errors={field.name: e}),
                    content_type="text/html",
                    status=400,
                )
        response_future = asyncio.Future()
        await self.process(
            {
                "request": request,
                "response_future": response_future,
                "status": 200,
                "form": data,
            }
        )
        return await response_future

    async def handle_post(self, request: Request):
        try:
            data = await self.extract_data(request)
        except json.decoder.JSONDecodeError:
            return aiohttp.web.Response(status=400, text="Invalid JSON")
        try:
            self.validate_field_presence(data)
        except ValueError as e:
            return aiohttp.web.Response(
                status=400,
                text=f"Schema validation error: {e}",
                content_type="text/plain",
            )
        return await self.clean_and_process(request, data)

    def extract_defaults(self, request: Request):
        defaults = {}
        for query_param, value in request.query.items():
            if "___" in query_param:
                parts = query_param.split("___")
                current_dict = defaults
                for fieldset in parts[:-1]:
                    if not current_dict.get(fieldset):
                        current_dict[fieldset] = {}
                    current_dict = current_dict[fieldset]
                current_dict[parts[-1]] = value
        return defaults

    def render_form(self, request: Request, errors={}):
        defaults = self.extract_defaults(request)

        for field in self.fields:
            if field.name in request.query:
                defaults[field.name] = request.query[field.name]
            elif field.name not in defaults:
                defaults[field.name] = field.default

        template = env.get_template("source-form.html")
        return template.render(
            form_intro=self.form_intro,
            fields=self.fields,
            defaults=defaults,
            errors=errors
        )


class ProtectedWebFormSource(WebFormSource):
    async def handle_request(self, request: Request):
        su = super()

        async def response_fn():
            return await su.handle_request(request)

        return await gate_response(
            request, lambda secret: self.test_secret(secret), response_fn
        )

    def test_secret(self, secret):
        return secret == self.Config["secret"]

    async def handle_post(self, request: Request):
        su = super()

        async def response_fn():
            return await su.handle_post(request)

        return await gate_response(
            request, lambda secret: self.test_secret(secret), response_fn
        )


class JWTWebFormSource(ProtectedWebFormSource):
    """
    ProtectedWebFormSource that is gated by a JWT token issued at some time and
    due to expire. All hidden fields are encoded in the JWT token and values
    for any other fields encoded in the JWT token take precedence over user
    input
    """

    def __init__(
        self,
        app,
        pipeline,
        connection="DefaultWebServerConnection",
        route="/",
        fields: Field = None,
        id=None,
        config=None,
        form_intro="",
    ):
        self.fields = fields + [IntField("exp", hidden=True, display="Expires at: ")]
        super().__init__(
            app,
            pipeline,
            connection,
            route,
            self.fields,
            id=id,
            config=config,
            form_intro="",
        )

    def test_secret(self, secret):
        print(self.Config["jwt-secret"])
        try:
            jwt.decode(secret, self.Config["jwt-secret"], algorithms=["HS256"])
        except DecodeError as e:
            print(e)
            return False
        except ExpiredSignatureError as e:
            print(e)
            return False
        return True

    def extract_defaults(self, request: Request):
        su = super()
        defaults = su.extract_defaults(request)
        defaults = recursive_merge(
            defaults,
            jwt.decode(
                request.query["secret"], self.Config["jwt-secret"], algorithms=["HS256"]
            ),
        )
        return defaults

    async def handle_post(self, request: Request):
        try:
            data = await self.extract_data(request)
        except json.decoder.JSONDecodeError:
            return aiohttp.web.Response(status=400, text="Invalid JSON")

        try:
            data = recursive_merge(
                data,
                jwt.decode(
                    request.query["secret"],
                    self.Config["jwt-secret"],
                    algorithms=["HS256"],
                ),
            )
        except DecodeError as e:
            return aiohttp.web.Response(text=f"Invalid secret: {e}", status=400)
        except ExpiredSignatureError as e:
            return aiohttp.web.Response(text=f"JWT Token expired: {e}", status=403)

        try:
            self.validate_field_presence(data)
        except ValueError as e:
            return aiohttp.web.Response(
                status=400,
                text=f"Schema validation error: {e}",
                content_type="text/plain",
            )

        return await self.clean_and_process(request, data)


class WebSink(Sink):
    """
    WebSink is a sink that sends HTTP requests.
    """

    def process(self, context, event):
        content_type = event.get("content_type", "text/html")
        # if bytes we use a binary response otherwise we use text
        if isinstance(event["response"], bytes):
            event["response_future"].set_result(
                aiohttp.web.Response(
                    status=event["status"],
                    body=event["response"],
                    content_type=content_type,
                )
            )
        else:
            event["response_future"].set_result(
                aiohttp.web.Response(
                    status=event["status"],
                    text=event["response"],
                    content_type=content_type,
                )
            )


class JSONWebSink(Sink):
    """
    JSONWebSink is a sink that sends HTTP requests with JSON content.
    """

    def process(self, context, event):
        """
        Process the incoming event and respond with either JSON or HTML.
        """
        if event["request"].content_type == "application/json":
            event["response_future"].set_result(
                aiohttp.web.json_response(event["response"], status=event["status"])
            )
        else:
            html_content = self.render_html_output(event["response"])
            event["response_future"].set_result(
                aiohttp.web.Response(
                    text=html_content,
                    content_type="text/html",
                    status=200,
                )
            )

    def render_html_output(self, json_data):
        template = env.get_template("output-form.html")
        fields_html = self.format_json_to_html(json_data)
        return template.render(fields=fields_html)

    def format_json_to_html(self, json_data):
        fields_html = []

        for key, value in json_data.items():
            if isinstance(value, dict):
                nested_html = self.format_json_to_html(value)
                template = env.get_template("nested-json.html")
                fields_html.append(template.render(key=key, content=nested_html))
            elif isinstance(value, list):
                nested_html = self.format_list(key, value)
                template = env.get_template("list.html")
                fields_html.append(template.render(key=key, content=nested_html))
            else:
                fields_html.append(self.format_key_value(key, value))

        return "".join(fields_html)

    def format_list(self, key, json_data_lst):
        list_items_html = []

        for item in json_data_lst:
            if isinstance(item, list):
                list_items_html.append(self.format_list(key, item))
            elif isinstance(item, dict):
                list_items_html.append(self.format_json_to_html(item))
            else:
                list_items_html.append(self.format_key_value(key, item))

        return "".join(list_items_html)

    def format_key_value(self, key, value):
        if isinstance(value, bool):
            fd = CheckboxField(key, readonly=True, default=value)
        elif isinstance(value, int):
            fd = IntField(key, readonly=True, default=value)
        else:
            fd = TextField(key, readonly=True, default=value)
        return fd.html()
