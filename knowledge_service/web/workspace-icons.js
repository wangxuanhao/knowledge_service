/* Local 24px line icons. All geometry is static; labels remain on their controls. */
(()=>{
  const paths={
    search:'M21 21l-5-5M19 11a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
    chat:'M21 11a8 8 0 0 1-8 8H7l-4 3V11a9 9 0 0 1 18 0ZM7 10h10M7 14h6',
    network:'M12 8V4M5 20v-5h14v5M12 15V8M9 2h6v6H9ZM2 20h6v3H2ZM16 20h6v3h-6Z',
    mindmap:'M10 12H4M14 12h6M12 10V4M12 14v6M9 9h6v6H9ZM2 10h2v4H2ZM20 10h2v4h-2ZM10 2h4v2h-4ZM10 20h4v2h-4Z',
    library:'M3 5h5v15H3ZM10 5h5v15h-5ZM17 4l4-1 3 15-4 1Z',
    records:'M7 3h10l4 4v14H3V3h4ZM16 3v5h5M7 12h10M7 16h7',
    ontology:'M12 3l9 5-9 5-9-5 9-5ZM3 12l9 5 9-5M3 16l9 5 9-5',
    runtime:'M8 4h8v4H8ZM4 16h6v5H4ZM15 16h6v5h-6ZM12 8v4M7 16v-4h11v4',
    quality:'M12 2l8 4v6c0 5-8 10-8 10S4 17 4 12V6l8-4ZM8 12l3 3 5-6',
    project:'M3 6h7l2 3h9v11H3V6ZM3 9h18',
    users:'M16 21v-3a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v3M13 6a4 4 0 1 1-8 0 4 4 0 0 1 8 0M22 21v-3a4 4 0 0 0-3-4M17 2a4 4 0 0 1 0 8',
    model:'M8 3h8v3h4v14H4V6h4V3ZM8 10v3M16 10v3M8 17h8M1 10v6M23 10v6',
    upload:'M12 16V3M7 8l5-5 5 5M3 15v6h18v-6',
    sliders:'M3 6h18M3 12h18M3 18h18M8 3v6M16 9v6M10 15v6',
    filter:'M3 4h18l-7 8v7l-4 2v-9L3 4Z',
    expand:'M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5',
    settings:'M9 3h6l1 4 4 1v8l-4 1-1 4H9l-1-4-4-1V8l4-1 1-4ZM16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0',
    'zoom-in':'M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0M6 10h8M10 6v8',
    'zoom-out':'M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0M6 10h8',
    maximize:'M3 9V3h6M15 3h6v6M21 15v6h-6M9 21H3v-6',
    panel:'M3 4h18v16H3V4ZM15 4v16',
    plus:'M12 4v16M4 12h16',
    refresh:'M20 8a8 8 0 1 0 0 8M20 3v5h-5',
    route:'M7 3a3 3 0 1 1-6 0 3 3 0 0 1 6 0M23 21a3 3 0 1 1-6 0 3 3 0 0 1 6 0M7 3h9a5 5 0 0 1 0 10H8a4 4 0 0 0 0 8h9',
    locate:'M12 2v4M12 18v4M2 12h4M18 12h4M19 12a7 7 0 1 1-14 0 7 7 0 0 1 14 0M14 12a2 2 0 1 1-4 0 2 2 0 0 1 4 0',
    chevron:'M9 5l7 7-7 7',
    collapse:'M3 4h18v16H3V4ZM9 4v16M16 9l-3 3 3 3',
    close:'M6 6l12 12M6 18L18 6'
  };
  const tabIcons={qa:'chat',search:'search',mindmap:'mindmap',sources:'library',records:'records','ontology-model':'ontology',runtime:'runtime',quality:'quality',projects:'project',useradmin:'users','llm-models':'model',ingest:'upload'};
  function create(name){
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
    for(const [key,value] of Object.entries({class:'workspace-icon',viewBox:'0 0 24 24',fill:'none',stroke:'currentColor','stroke-width':'1.7','stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true',focusable:'false'}))svg.setAttribute(key,value);
    const path=document.createElementNS('http://www.w3.org/2000/svg','path');path.setAttribute('d',paths[name]||paths.panel);svg.append(path);return svg;
  }
  function decorate(button,name){
    if(!button)return null;
    let icon=button.querySelector('.workspace-icon');
    if(icon){const replacement=create(name);icon.replaceChildren(...replacement.children);}else{icon=create(name);button.prepend(icon);}
    return icon;
  }
  window.WorkspaceIcons={create,decorate,tabIcons,names:Object.keys(paths)};
})();
