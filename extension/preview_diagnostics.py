"""Preview-only typed resolution and actionable, source-grounded notices."""
from pathlib import PurePosixPath


def resolve_path(assets,path):
    """Only the exact same-directory .sanvfx -> .sanbeam mismatch is allowed."""
    key=path.replace('\\','/').lower()
    if key in assets.records:return key,None
    if key.endswith('.sanvfx'):
        candidate=str(PurePosixPath(key).with_suffix('.sanbeam'))
        if candidate in assets.records:
            return candidate,'Preview-only beam extension resolution: '+path+' -> '+candidate
    return key,None


RULES={
 'Missing reference':('missing_reference','Referenced asset is not available in indexed game/project files.','Refresh the index and verify this exact path in the project/game build; no replacement was invented.'),
 'Preview-only beam extension resolution':('beam_extension','A particle suffix names an existing same-path beam asset.','Preview uses the verified .sanbeam only; the original Lua/export reference is unchanged. Its separate impact may still be missing.'),
 'Source renderer disabled':('renderer_disabled','The exported source disables every renderer.','This entry has no standalone visible renderer; choose its actual visual effect, not a helper/control node.'),
 'No emitted particles in sampled clip':('no_emission','The source emits no visible particles during this bounded clip.','Check emission, triggers and source lifetime/delay; simulation cannot execute game callbacks or sub-emitters.'),
 'Zero exported trail width (runtime override unverified)':('zero_trail_width','The exported trail width is zero.','Verify the same effect in-game or inspect reconstruction/runtime setup; no nonzero width is guessed.'),
 'Mesh/noise shader layer':('mesh_noise_shader','Packed mesh/noise shader layers are omitted.','Native masking, dissolve and custom streams require shader behavior or a matched game capture; raw noise would invent solid shapes.'),
 'Screen-space distortion':('screen_distortion','Scene-dependent distortion is omitted.','Compare using a matched game background/camera; a dark billboard GIF cannot reproduce screen refraction.'),
 'Procedural flare profile approximated':('procedural_flare','Procedural flare profile is approximated, not shader-rendered.','Use a matched game capture for halo/arms/focus validation; the source tint is retained but parity is not claimed.'),
 'Solid-frame shader layer':('solid_frame_shader','This solid-frame shader layer is not implemented.','Use the in-game renderer or verified shader semantics rather than an invented atlas sprite.'),
}


def enrich(info,extra=()):
    result=dict(info);result['limitations']=sorted(set(info.get('limitations',()))|set(extra))
    diagnostics=[];seen=set()
    for limitation in result['limitations']:
        prefix=limitation.split(':',1)[0]
        code,message,action=RULES.get(prefix,('source_limitation',limitation,'Validate this source case in-game; this preview is not a game capture.'))
        if code in seen:continue
        seen.add(code);diagnostics.append({'code':code,'message':message,'action':action})
    motion=result.get('motion')
    if motion:
        diagnostics.insert(0,{'code':'moving_emitter_demo','message':f"Moving-emitter demonstration: {motion['speed_units_per_second']:g} source units/s.",
                             'action':('Raise Demo emitter speed above zero to emit distance-based particles.' if motion['speed_units_per_second']==0 else
                                       'Preview movement is a controlled demonstration, not inferred game speed; native distance emission/lifetime parameters are retained.')})
    result['diagnostics']=diagnostics
    return result


def messages(info):
    return [d['message']+' '+d['action'] for d in info.get('diagnostics',())]
