# A basic mkdocstrings handler for {fmt}.
# Copyright (c) 2012 - present, Victor Zverovich

import os
import xml.etree.ElementTree as et
from mkdocstrings.handlers.base import BaseHandler
from typing import Any, Mapping, Optional
from subprocess import CalledProcessError, PIPE, Popen, STDOUT

class Definition:
  '''A definition extracted by Doxygen.'''
  def __init__(self, name: str):
    self.name = name

# A map from Doxygen to HTML tags.
tag_map = {
  'bold': 'b',
  'computeroutput': 'code',
  'para': 'p',
  'programlisting': 'pre',
  'verbatim': 'pre'
}

# A map from Doxygen tags to text.
tag_text_map = {
  'codeline': '',
  'highlight': '',
  'sp': ' '
}

def doxyxml2html(nodes: list[et.Element]):
  out = ''
  for n in nodes:
    tag = tag_map.get(n.tag)
    if not tag:
      out += tag_text_map[n.tag]
    out += '<' + tag + '>' if tag else ''
    out += '<code>' if tag == 'pre' else ''
    if n.text:
      out += n.text
    out += doxyxml2html(n)
    out += '</code>' if tag == 'pre' else ''
    out += '</' + tag + '>' if tag else ''
    if n.tail:
      out += n.tail
  return out

def get_template_params(node: et.Element) -> Optional[list[Definition]]:
  templateparamlist = node.find('templateparamlist')
  if templateparamlist is None:
    return None
  params = []
  for param_node in templateparamlist.findall('param'):
    name = param_node.find('declname')
    param = Definition(name.text if name is not None else '')
    param.type = param_node.find('type').text
    params.append(param)
  return params

def convert_param(param: et.Element) -> Definition:
  d = Definition(param.find('declname').text)
  type = param.find('type')
  type_str = type.text if type.text else ''
  for ref in type:
    type_str += ref.text
    if ref.tail:
      type_str += ref.tail
  type_str += type.tail.strip()
  type_str = type_str.replace('< ', '<').replace(' >', '>')
  type_str = type_str.replace(' &', '&').replace(' *', '*')
  d.type = type_str
  return d

class CxxHandler(BaseHandler):
  def __init__(self, **kwargs: Any) -> None:
    super().__init__(handler='cxx', **kwargs)

    # Run doxygen.
    cmd = ['doxygen', '-']
    doc_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    include_dir = os.path.join(os.path.dirname(doc_dir), 'include', 'fmt')
    self._doxyxml_dir = 'doxyxml'
    p = Popen(cmd, stdin=PIPE, stdout=PIPE, stderr=STDOUT)
    out, _ = p.communicate(input=r'''
        PROJECT_NAME      = fmt
        GENERATE_LATEX    = NO
        GENERATE_MAN      = NO
        GENERATE_RTF      = NO
        CASE_SENSE_NAMES  = NO
        INPUT             = {0}/args.h {0}/base.h {0}/chrono.h {0}/color.h \
                            {0}/core.h {0}/compile.h {0}/format.h {0}/os.h \
                            {0}/ostream.h {0}/printf.h {0}/ranges.h {0}/std.h \
                            {0}/xchar.h
        QUIET             = YES
        JAVADOC_AUTOBRIEF = NO
        AUTOLINK_SUPPORT  = NO
        GENERATE_HTML     = NO
        GENERATE_XML      = YES
        XML_OUTPUT        = {1}
        ALIASES           = "rst=\verbatim embed:rst"
        ALIASES          += "endrst=\endverbatim"
        MACRO_EXPANSION   = YES
        PREDEFINED        = _WIN32=1 \
                            __linux__=1 \
                            FMT_ENABLE_IF(...)= \
                            FMT_USE_VARIADIC_TEMPLATES=1 \
                            FMT_USE_RVALUE_REFERENCES=1 \
                            FMT_USE_USER_DEFINED_LITERALS=1 \
                            FMT_USE_ALIAS_TEMPLATES=1 \
                            FMT_USE_NONTYPE_TEMPLATE_ARGS=1 \
                            FMT_API= \
                            "FMT_BEGIN_NAMESPACE=namespace fmt {{" \
                            "FMT_END_NAMESPACE=}}" \
                            "FMT_STRING_ALIAS=1" \
                            "FMT_VARIADIC(...)=" \
                            "FMT_VARIADIC_W(...)=" \
                            "FMT_DOC=1"
        EXCLUDE_SYMBOLS   = fmt::formatter fmt::printf_formatter fmt::arg_join \
                            fmt::basic_format_arg::handle
        '''.format(include_dir, self._doxyxml_dir).encode('utf-8'))
    if p.returncode != 0:
        raise CalledProcessError(p.returncode, cmd)

    # Load XML.
    with open(os.path.join(self._doxyxml_dir, 'namespacefmt.xml')) as f:
      self._doxyxml = et.parse(f)

  def collect(self, identifier: str, config: Mapping[str, Any]) -> Definition:
    name = identifier
    paren = name.find('(')
    param_str = None
    if paren > 0:
      name, param_str = name[:paren], name[paren + 1:-1]
      
    nodes = self._doxyxml.findall(
      f"compounddef/sectiondef/memberdef/name[.='{name}']/..")
    candidates = []
    for node in nodes:
      params = [convert_param(p) for p in node.findall('param')]
      node_param_str = ', '.join([p.type for p in params])
      if param_str and param_str != node_param_str:
        candidates.append(f'{name}({node_param_str})')
        continue
      d = Definition(name)
      d.type = node.find('type').text
      d.template_params = get_template_params(node)
      d.params = params
      d.desc = node.findall('detaileddescription/para')
      return d
    cls = self._doxyxml.findall(f"compounddef/innerclass[.='fmt::{name}']")
    if not cls:
      raise Exception(f'Cannot find {identifier}. Candidates: {candidates}')
    with open(os.path.join(self._doxyxml_dir, cls[0].get('refid') + '.xml')) as f:
      xml = et.parse(f)
      node = xml.find('compounddef')
      d = Definition(name)
      d.type = node.get('kind')
      d.template_params = get_template_params(node)
      d.params = None
      d.desc = node.findall('detaileddescription/para')
      return d

  def render(self, d: Definition, config: dict) -> str:
    text = '<div class="docblock">\n'
    text += '<pre><code>'
    if d.template_params is not None:
      text += 'template &lt;'
      text += ', '.join(
        [f'{p.type} {p.name}'.rstrip() for p in d.template_params])
      text += '&gt;\n'
    text += d.type + ' ' + d.name
    if d.params is not None:
      params = ', '.join(
        [f'{p.type.replace("<", "&lt;")} {p.name}' for p in d.params])
      text += '(' + params + ')'
    text += ';'
    text += '</code></pre>\n'
    text += '<div class="docblock-desc">\n'
    desc = doxyxml2html(d.desc)
    text += desc
    text += '</div>\n'
    text += '</div>\n'
    return text

def get_handler(theme: str, custom_templates: Optional[str] = None,
                **config: Any) -> CxxHandler:
  '''Return an instance of `CxxHandler`.

  Arguments:
    theme: The theme to use when rendering contents.
    custom_templates: Directory containing custom templates.
    **config: Configuration passed to the handler.
  '''
  return CxxHandler(theme=theme, custom_templates=custom_templates)
