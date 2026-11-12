"""Run checker assertions and reject files that only define uncalled tests."""
import ast
import hashlib
from pathlib import Path
import sys


def run(path):
    path=Path(path).resolve()
    source=path.read_text('utf-8')
    tree=ast.parse(source,filename=str(path))
    marker='__study_assert_'+hashlib.sha256(source.encode()).hexdigest()[:16]
    count=0

    def seen():
        nonlocal count
        count+=1

    class Instrument(ast.NodeTransformer):
        def visit_Assert(self,node):
            # A literal true is never evidence of checking a result.
            if isinstance(node.test,ast.Constant) and bool(node.test.value):
                return node
            observation=ast.Expr(value=ast.Call(func=ast.Name(id=marker,ctx=ast.Load()),args=[],keywords=[]))
            ast.copy_location(observation,node)
            return [observation,node]

    tree=ast.fix_missing_locations(Instrument().visit(tree))
    namespace={'__name__':'__main__','__file__':str(path),'__package__':None,marker:seen}
    try:
        exec(compile(tree,str(path),'exec'),namespace)
    except SystemExit as error:
        if error.code not in (None,0):
            raise
    if not count:
        raise RuntimeError('Жоден змістовний assert не виконався: '+path.name+'. Викличте функції перевірки; assert True не рахується.')
    print('STUDY_ASSERTS_EXECUTED',path.name,count,flush=True)
    return count


if __name__=='__main__':
    run(sys.argv[1])
