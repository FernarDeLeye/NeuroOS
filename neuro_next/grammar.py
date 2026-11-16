import json
class ServerSchemaCompiler:
    mode='server-response-format'
    sha256='server-owned-schema-conversion'
    def __call__(self,schema):
        # Honest metadata: no local GBNF binary or fake compilation. Server must
        # accept response_format; doctor --probe tests a real constrained result.
        return json.dumps(schema,separators=(',',':'),allow_nan=False)


GrammarCompiler=ServerSchemaCompiler
