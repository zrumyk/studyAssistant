"""Compile our finite Pydantic schemas to DeepSeek strict function schemas."""
import copy


def deepseek_schema(schema):
    definitions=schema.get('$defs',{})
    def visit(node,seen=()):
        node=copy.deepcopy(node)
        if '$ref' in node:
            reference=node['$ref']
            name=reference.removeprefix('#/$defs/')
            if not reference.startswith('#/$defs/') or name not in definitions or name in seen:
                raise ValueError('Unsupported or recursive response schema')
            return visit(definitions[name],seen+(name,))
        result={key:value for key,value in node.items() if key in ('type','description','enum','pattern','format','minimum','maximum','exclusiveMinimum','exclusiveMaximum','multipleOf')}
        if 'const' in node:
            result['enum']=[node['const']]
        constraints=[f'{key}={node[key]}' for key in ('minLength','maxLength','minItems','maxItems') if key in node]
        if constraints:
            result['description']=(result.get('description','')+'; '+', '.join(constraints)).strip('; ')
        if node.get('type')=='object':
            result['properties']={key:visit(value,seen) for key,value in node.get('properties',{}).items()}
            result['required']=list(result['properties'])
            result['additionalProperties']=False
        elif node.get('type')=='array':
            result['items']=visit(node['items'],seen)
        elif 'anyOf' in node:
            result['anyOf']=[visit(value,seen) for value in node['anyOf']]
        elif node.get('type') not in ('string','number','integer','boolean'):
            raise ValueError('Unsupported response schema type')
        return result
    return visit(schema)
