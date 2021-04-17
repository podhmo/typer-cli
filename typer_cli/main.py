import importlib.util
import re
import sys
from pathlib import Path
from typing import Any, Iterable, List, Optional, Tuple, cast

import click
import click.core
import typer
from click import Command, Group, Option
from click._bashcomplete import resolve_ctx  # type: ignore
from click._bashcomplete import get_choices as original_get_choices

from . import __version__

default_app_names = ("app", "cli", "main")
default_func_names = ("main", "cli", "app")

app = typer.Typer()
utils_app = typer.Typer(help="Extra utility commands for Typer apps.")
app.add_typer(utils_app, name="utils")


class State:
    def __init__(self) -> None:
        self.app: Optional[str] = None
        self.func: Optional[str] = None
        self.file: Optional[Path] = None
        self.module: Optional[str] = None


state = State()


def maybe_update_state(ctx: click.Context) -> None:
    path_or_module = ctx.params.get("path_or_module")
    if path_or_module:
        file_path = Path(path_or_module)
        if file_path.exists() and file_path.is_file():
            state.file = file_path
        else:
            if not re.fullmatch(r"[a-zA-Z_]\w*(\.[a-zA-Z_]\w*)*", path_or_module):
                typer.echo(
                    f"Not a valid file or Python module: {path_or_module}", err=True
                )
                sys.exit(1)
            state.module = path_or_module
    app_name = ctx.params.get("app")
    if app_name:
        state.app = app_name
    func_name = ctx.params.get("func")
    if func_name:
        state.func = func_name


class TyperCLIGroup(click.Group):
    def list_commands(self, ctx: click.Context) -> Iterable[str]:
        self.maybe_add_run(ctx)
        return super().list_commands(ctx)

    def get_command(self, ctx: click.Context, name: str) -> Optional[Command]:
        self.maybe_add_run(ctx)
        return super().get_command(ctx, name)

    def invoke(self, ctx: click.Context) -> Any:
        self.maybe_add_run(ctx)
        return super().invoke(ctx)

    def maybe_add_run(self, ctx: click.Context) -> None:
        maybe_update_state(ctx)
        maybe_add_run_to_cli(self)


def get_typer_from_module(module: Any) -> Optional[typer.Typer]:
    # Try to get defined app
    if state.app:
        obj: typer.Typer = getattr(module, state.app, None)
        if not isinstance(obj, typer.Typer):
            typer.echo(f"Not a Typer object: --app {state.app}", err=True)
            sys.exit(1)
        return obj
    # Try to get defined function
    if state.func:
        func_obj = getattr(module, state.func, None)
        if not callable(func_obj):
            typer.echo(f"Not a function: --func {state.func}", err=True)
            sys.exit(1)
        sub_app = typer.Typer()
        sub_app.command()(func_obj)
        return sub_app
    # Iterate and get a default object to use as CLI
    local_names = dir(module)
    local_names_set = set(local_names)
    # Try to get a default Typer app
    for name in default_app_names:
        if name in local_names_set:
            obj = getattr(module, name, None)
            if isinstance(obj, typer.Typer):
                return obj
    # Try to get any Typer app
    for name in local_names_set - set(default_app_names):
        obj = getattr(module, name)
        if isinstance(obj, typer.Typer):
            return obj
    # Try to get a default function
    for func_name in default_func_names:
        func_obj = getattr(module, func_name, None)
        if callable(func_obj):
            sub_app = typer.Typer()
            sub_app.command()(func_obj)
            return sub_app
    # Try to get any func app
    for func_name in local_names_set - set(default_func_names):
        func_obj = getattr(module, func_name)
        if callable(func_obj):
            sub_app = typer.Typer()
            sub_app.command()(func_obj)
            return sub_app
    return None


def get_typer_from_state() -> Optional[typer.Typer]:
    spec = None
    if state.file:
        module_name = state.file.name
        spec = importlib.util.spec_from_file_location(module_name, str(state.file))
    elif state.module:
        spec = importlib.util.find_spec(state.module)  # type: ignore
    if spec is None:
        if state.file:
            typer.echo(f"Could not import as Python file: {state.file}", err=True)
        else:
            typer.echo(f"Could not import as Python module: {state.module}", err=True)
        sys.exit(1)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore
    obj = get_typer_from_module(module)
    return obj


def maybe_add_run_to_cli(cli: click.Group) -> None:
    if "run" not in cli.commands:
        if state.file or state.module:
            obj = get_typer_from_state()
            if obj:
                obj._add_completion = False
                click_obj = typer.main.get_command(obj)
                click_obj.name = "run"
                if not click_obj.help:
                    click_obj.help = "Run the provided Typer app."
                cli.add_command(click_obj)


def get_choices(
    cli: Command, prog_name: str, args: List[str], incomplete: str
) -> List[Tuple[str, str]]:
    ctx: typer.Context = resolve_ctx(cli, prog_name, args)
    if ctx.parent is None:
        assert isinstance(cli, Group)
        cli = cast(Group, cli)
        maybe_update_state(ctx)
        maybe_add_run_to_cli(cli)
    return original_get_choices(cli, prog_name, args, incomplete)


def print_version(ctx: click.Context, param: Option, value: bool) -> None:
    if not value or ctx.resilient_parsing:
        return
    typer.echo(f"Typer CLI version: {__version__}")
    raise typer.Exit()


@app.callback(cls=TyperCLIGroup)
def callback(
    ctx: typer.Context,
    *,
    path_or_module: str = typer.Argument(None),
    app: str = typer.Option(None, help="The typer app object/variable to use."),
    func: str = typer.Option(None, help="The function to convert to Typer."),
    version: bool = typer.Option(
        False, "--version", help="Print version and exit.", callback=print_version  # type: ignore
    ),
) -> None:
    """
    Typer CLI.

    Run Typer scripts with completion, without having to create a package.

    You probably want to install completion for the typer command:

    $ typer --install-completion

    https://typer.tiangolo.com/
    """
    maybe_update_state(ctx)


def get_docs_for_click(
    *,
    obj: Command,
    ctx: typer.Context,
    indent: int = 0,
    name: str = "",
    call_prefix: str = "",
) -> str:
    docs = "#" * (1 + indent)
    command_name = name or obj.name
    if call_prefix:
        command_name = f"{call_prefix} {command_name}"
    title = f"`{command_name}`" if command_name else "CLI"
    docs += f" {title}\n\n"
    if obj.help:
        docs += f"{obj.help}\n\n"
    usage_pieces = obj.collect_usage_pieces(ctx)
    if usage_pieces:
        docs += "**Usage**:\n\n"
        docs += "```console\n"
        docs += "$ "
        if command_name:
            docs += f"{command_name} "
        docs += f"{' '.join(usage_pieces)}\n"
        docs += "```\n\n"
    args = []
    opts = []
    for param in obj.get_params(ctx):
        rv = param.get_help_record(ctx)
        if rv is not None:
            if param.param_type_name == "argument":
                args.append(rv)
            elif param.param_type_name == "option":
                opts.append(rv)
    if args:
        docs += f"**Arguments**:\n\n"
        for arg_name, arg_help in args:
            docs += f"* `{arg_name}`"
            if arg_help:
                docs += f": {arg_help}"
            docs += "\n"
        docs += "\n"
    if opts:
        docs += f"**Options**:\n\n"
        for opt_name, opt_help in opts:
            docs += f"* `{opt_name}`"
            if opt_help:
                docs += f": {opt_help}"
            docs += "\n"
        docs += "\n"
    if obj.epilog:
        docs += f"{obj.epilog}\n\n"
    if isinstance(obj, Group):
        group: Group = cast(Group, obj)
        commands = group.list_commands(ctx)
        if commands:
            docs += f"**Commands**:\n\n"
            for command in commands:
                command_obj = group.get_command(ctx, command)
                assert command_obj
                docs += f"* `{command_obj.name}`"
                command_help = command_obj.get_short_help_str()
                if command_help:
                    docs += f": {command_help}"
                docs += "\n"
            docs += "\n"
        for command in commands:
            command_obj = group.get_command(ctx, command)
            assert command_obj
            use_prefix = ""
            if command_name:
                use_prefix += f"{command_name}"
            docs += get_docs_for_click(
                obj=command_obj, ctx=ctx, indent=indent + 1, call_prefix=use_prefix
            )
    return docs


@utils_app.command()
def docs(
    ctx: typer.Context,
    name: str = typer.Option("", help="The name of the CLI program to use in docs."),
    output: Path = typer.Option(
        None,
        help="An output file to write docs to, like README.md.",
        file_okay=True,
        dir_okay=False,
    ),
) -> None:
    """
    Generate Markdown docs for a Typer app.
    """
    typer_obj = get_typer_from_state()
    if not typer_obj:
        typer.echo(f"No Typer app found", err=True)
        raise typer.Abort()
    click_obj = typer.main.get_command(typer_obj)
    docs = get_docs_for_click(obj=click_obj, ctx=ctx, name=name)
    clean_docs = f"{docs.strip()}\n"
    if output:
        output.write_text(clean_docs)
        typer.echo(f"Docs saved to: {output}")
    else:
        typer.echo(clean_docs)


@utils_app.command()
def init(
    ctx: typer.Context,
    *,
    inplace: bool = False,
) -> None:
    """
    initialize Typer
    """
    from typing import Union
    import textwrap
    from lib2to3 import pytree
    from lib2to3 import pygram
    from lib2to3.pgen2 import driver
    from lib2to3.pgen2 import token
    from lib2to3.pgen2.parse import ParseError

    Node = Union[pytree.Node, pytree.Leaf]

    def node_name(node: Node) -> str:
        # Nodes with values < 256 are tokens. Values >= 256 are grammar symbols.
        if node.type < 256:
            return token.tok_name[node.type]
        else:
            return pygram.python_grammar.number2symbol[node.type]

    class PyTreeVisitor:
        def __init__(self):
            self.import_typer_inserted = False
            self.seen = set()

        def visit(self, node: Node) -> None:
            method = "visit_{0}".format(node_name(node))
            if hasattr(self, method):
                # Found a specific visitor for this node
                if getattr(self, method)(node):
                    return

            self.default_node_visit(node)  # type: ignore

        def default_node_visit(self, node: pytree.Node) -> None:
            for child in node.children:
                self.visit(child)

        def visit_decorator(self, node: Node) -> None:
            this = node
            while True:
                this = this.next_sibling
                token = node_name(this)
                if token == "classdef":
                    return
                if token == "funcdef":
                    break

            k = id(this)
            if k in self.seen:
                return

            self.seen.add(k)
            if not self.import_typer_inserted:
                self.import_typer_inserted = True
                prefix = textwrap.dedent(
                    f"""\
                import typer

                app = typer.Typer(help="Awesome CLI")


                {prefix}"""
                )

            node.prefix = f"{node.prefix}@app.command()\n"

        def visit_funcdef(self, node: Node) -> None:
            k = id(node)
            if k in self.seen:
                return

            self.seen.add(k)
            prefix = f"{node.prefix}@app.command()\n"

            if not self.import_typer_inserted:
                self.import_typer_inserted = True
                prefix = textwrap.dedent(
                    f"""\
                import typer

                app = typer.Typer(help="Awesome CLI")


                {prefix}"""
                )

            node.prefix = prefix

    module_name = "_type_cli__init_main"
    spec = importlib.util.spec_from_file_location(module_name, state.file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    public_functions = []
    for name, val in module.__dict__.items():
        if name.startswith("_"):
            continue
        if not callable(val):
            continue
        if getattr(val, "__module__", None) != module.__name__:
            continue
        if hasattr(val, "mro"):
            continue
        public_functions.append(val)

    driver = driver.Driver(
        pygram.python_grammar_no_print_statement, convert=pytree.convert
    )
    t = driver.parse_file(state.file, debug=True)
    PyTreeVisitor().visit(t)
    print(t)
    print(
        textwrap.dedent(
            """
    if __name__ == "__main__":
        app()
"""
        )
    )


def main() -> Any:
    click._bashcomplete.get_choices = get_choices
    return app()
