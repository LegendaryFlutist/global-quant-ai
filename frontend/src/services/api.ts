let csrf='';
export function setCSRF(value:string){csrf=value;}
export async function api<T>(path:string,options:RequestInit={}):Promise<T>{
 const response=await fetch('/api'+path,{...options,credentials:'same-origin',headers:{'Content-Type':'application/json',...(csrf?{'X-CSRF-Token':csrf}:{}),...options.headers}});
 const body=await response.json().catch(()=>({detail:'服务响应无效'}));
 if(!response.ok){if(response.status===401&&!path.startsWith('/auth/'))window.dispatchEvent(new Event('session-expired'));throw new Error(typeof body.detail==='string'?body.detail:JSON.stringify(body.detail));}
 return body as T;
}
export const post=<T,>(path:string,body:unknown)=>api<T>(path,{method:'POST',body:JSON.stringify(body)});
export function date(value:number|string|null|undefined,timezone='Asia/Shanghai') {if(!value)return '—';return new Date(value).toLocaleString('zh-CN',{timeZone:timezone,hour12:false});}
