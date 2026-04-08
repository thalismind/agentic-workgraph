from workgraph import create_app, node, workflow


@node(id="hello")
async def hello(name: str, ctx):
    return f"hello {name}"


@workflow(name="hello-flow")
def hello_flow():
    return hello(name=["world"])


app = create_app(workflows=[hello_flow])
