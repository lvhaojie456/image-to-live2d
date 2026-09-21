// SPDX-License-Identifier: GPL-3.0-only
// Integrates with the GPL-3.0 psd2live engine; kept separate from its pinned checkout.
package io.github.psd2live.agentkit

import io.github.psd2live.core.*
import kotlinx.serialization.json.*
import org.umamo.format.art.SourceArt
import org.umamo.runtime.model.*
import java.nio.file.Files
import java.nio.file.Path
import kotlin.math.*

private fun JsonObject.num(key: String): Float = getValue(key).jsonPrimitive.float.also { require(it.isFinite()) }
private fun JsonObject.point(key: String): Pair<Float,Float> = getValue(key).jsonArray.let {
    require(it.size == 2); it[0].jsonPrimitive.float to it[1].jsonPrimitive.float
}
private fun smooth(value: Float): Float { val v=value.coerceIn(0f,1f); return v*v*(3-2*v) }
private fun curveY(points: JsonArray, x: Float): Float {
    val p=points.map { it.jsonArray.map { n->n.jsonPrimitive.float } }
    if(x<=p.first()[0])return p.first()[1]
    if(x>=p.last()[0])return p.last()[1]
    val i=p.indexOfFirst { it[0]>=x };val a=p[i-1];val b=p[i]
    return a[1]+(b[1]-a[1])*(x-a[0])/(b[0]-a[0])
}

fun withProceduralMotion(source: SourceArt, initial: PipelineConfig, settings: JsonObject, output: Path): PipelineConfig {
    val body = settings.getValue("body").jsonObject
    val arms = settings.getValue("arms").jsonObject
    val skirt = settings.getValue("skirt").jsonObject
    val garmentNames=skirt["target_layers"]?.jsonArray?.map { it.jsonPrimitive.content }?.toSet() ?: setOf("bottomwear")
    val garmentLabel=skirt["display_name"]?.jsonPrimitive?.content ?: "裙摆晃动"
    val mouthNames = setOf("mouth_close","mouth_open","lip_upper","lip_lower","tooth-t","tongue")
    val expressionRig=settings["expressions"]?.takeUnless { it is JsonNull }?.jsonObject
    val armNames = setOf("arm-l","arm-r","hand-l","hand-r")
    val required = armNames + garmentNames + setOf("topwear","legwear-l","legwear-r")
    require(source.layers.map { it.name }.containsAll(required)) { "Missing required body layers" }
    val byName=source.layers.associateBy { it.name }
    val overrides=source.layers.filter { it.name in mouthNames }.associate {
        it.id.raw to LayerClassificationOverride(
            if(expressionRig==null) { if(it.name=="mouth_close") SemanticTag.MOUTH_CLOSE else SemanticTag.FACE_DETAIL }
            else when(it.name) {
                "mouth_close"->SemanticTag.MOUTH_CLOSE
                "mouth_open"->SemanticTag.MOUTH_OPEN
                "tooth-t"->SemanticTag.TOOTH_T
                "tongue"->SemanticTag.TONGUE
                else->SemanticTag.FACE_DETAIL
            },Side.NONE)
    }
    // Put both sleeve and hand in the same coordinate system before adding the shared warp.
    val parents=source.layers.filter { it.name in armNames || it.name in garmentNames || (expressionRig!=null && it.name in mouthNames) }.associate {
        it.id.raw to if(it.name in mouthNames) "DeformMouthShapeBoth" else "DeformBodyZBreath"
    }
    var config=initial.copy(parentOverrides=initial.parentOverrides+parents,
        meshOverrides=initial.meshOverrides+(if(expressionRig==null) emptyMap() else source.layers.filter {
            it.name in mouthNames || it.name.startsWith("eye") || it.name.startsWith("irides")
        }.associate { it.id.raw to MeshSettings(maxEdgeDistance=2f,interiorDensity=2f) }),
        layerOverrides=initial.layerOverrides+overrides, preserveSourceRaster=true,
        sourceClosedEyes=true, mouthOutlineEnabled=false, initialHeadAngleZOverride=0f,
        rigEdits=initial.rigEdits.copy(assetLayers=initial.rigEdits.assetLayers+
            source.layers.associate { it.id.raw to buildJsonObject {} }))
    val base=PSD2LivePipeline().buildPreview(source,config)
    val character=base.analysis.anchors.character
    fun canvas(u:Float,v:Float)=character.left+u*character.width to character.top+v*character.height
    fun local(x:Float,y:Float)=(x-character.left)/character.width to (y-character.top)/character.height
    fun meshIds(names:Set<String>)=base.rig.puppet.drawables.filter { d ->
        source.layers.any { it.name in names && it.id.raw==base.rig.layerIdByDrawableId[d.id.raw] }
    }.map { it.id.raw }
    val warps = listOf(
        RigWarpEdit("DeformArmSwingL","左手臂轻摆","DeformBodyZBreath",meshIds(setOf("arm-l","hand-l")),24,8),
        RigWarpEdit("DeformArmSwingR","右手臂轻摆","DeformBodyZBreath",meshIds(setOf("arm-r","hand-r")),24,8),
        RigWarpEdit("DeformSkirtSwing",garmentLabel,"DeformBodyZBreath",meshIds(garmentNames),24,8)
    )
    val expanded=RigEditOverlay(warpEdits=warps).applyTo(base.rig.puppet)
    val keys=mutableListOf<RigKeyformSetEdit>()
    fun warpKeys(id:String, coordinate:Map<String,Float>, mapping:(Float,Float)->Pair<Float,Float>) {
        val warp=expanded.deformers.single { it.id.raw==id } as Deformer.Warp
        val points=mutableListOf<Float>()
        for(r in 0..warp.rows) for(c in 0..warp.columns) {
            val (x,y)=canvas(c.toFloat()/warp.columns,r.toFloat()/warp.rows)
            val (tx,ty)=mapping(x,y); val (u,v)=local(tx,ty)
            points+=u;points+=v
        }
        keys+=RigKeyformSetEdit(RigTargetRef(RigTargetKind.WARP_DEFORMER,id),coordinate,
            RigKeyformGeometryEdit(controlPoints=points))
    }
    val (hipX,hipY)=body.point("pivot")
    val lift=body.num("breath_lift_px")
    val expansion=body.num("chest_expansion")
    val leanDegrees=body.num("lean_degrees")
    require(leanDegrees in 0.1f..6f && lift in 0.1f..12f && expansion in 0f..0.03f)
    for(z in listOf(-10f,0f,10f)) for(b in listOf(0f,0.5f,1f)) {
        warpKeys("DeformBodyZBreath",mapOf("ParamBodyAngleZ" to z,"ParamBreath" to b)) { x,y ->
            val topWeight=smooth((body.num("breath_pin_y")-y)/(body.num("breath_pin_y")-body.num("shoulder_y")))
            val chest=exp(-((y-body.num("chest_y"))/body.num("chest_radius")).pow(2))
            val bx=x+(x-hipX)*expansion*chest*b
            val by=y-lift*topWeight*b
            val weight=1-smooth((y-body.num("lean_full_y"))/(body.num("lean_pin_y")-body.num("lean_full_y")))
            val angle=z/10f*leanDegrees*(PI/180f).toFloat()
            val rx=hipX+(bx-hipX)*cos(angle)-(by-hipY)*sin(angle)
            val ry=hipY+(bx-hipX)*sin(angle)+(by-hipY)*cos(angle)
            (bx+(rx-bx)*weight) to (by+(ry-by)*weight)
        }
    }
    for(side in listOf("l","r")) {
        val spec=arms.getValue(side).jsonObject
        val (px,py)=spec.point("pivot")
        val amplitude=spec.num("degrees")
        require(amplitude in 0.1f..6f && spec.num("free_y")>spec.num("pin_y"))
        val id=if(side=="l") "L" else "R"
        for(v in listOf(-1f,0f,1f)) {
            warpKeys("DeformArmSwing$id",mapOf("ParamArm${id}Swing" to v)) { x,y ->
                val weight=smooth((y-spec.num("pin_y"))/(spec.num("free_y")-spec.num("pin_y")))
                val angle=v*amplitude*(PI/180f).toFloat()
                val rx=px+(x-px)*cos(angle)-(y-py)*sin(angle)
                val ry=py+(x-px)*sin(angle)+(y-py)*cos(angle)
                (x+(rx-x)*weight) to (y+(ry-y)*weight)
            }
        }
    }
    require(skirt.num("hem_y")>skirt.num("pin_y") && skirt.num("sway_px") in 0.1f..20f)
    for(v in listOf(-1f,-0.5f,0f,0.5f,1f)) {
        warpKeys("DeformSkirtSwing",mapOf("ParamSkirtSwing" to v)) { x,y ->
            val t=((y-skirt.num("pin_y"))/(skirt.num("hem_y")-skirt.num("pin_y"))).coerceIn(0f,1f)
            val weight=t*t
            (x+v*skirt.num("sway_px")*weight) to
                (y-v*skirt.num("hem_lift_px")*((x-hipX)/skirt.num("half_width"))*weight)
        }
    }
    // Explicit eye keys close the actual eye geometry before the painted endpoint
    // appears. The cleaned face underneath never contains a second baked-in eye.
    if(expressionRig!=null) {
        val eyes=expressionRig.getValue("eyes").jsonObject
        for(side in listOf("l","r")) {
            val spec=eyes[side]?.jsonObject ?: continue
            val eyeNames=setOf("eyewhite-$side","irides-$side","eyelash-$side","eye_close-$side")
            val parameter=if(side=="l") "ParamEyeLOpen" else "ParamEyeROpen"
            for(d in base.rig.puppet.drawables) {
                val sourceId=base.rig.layerIdByDrawableId[d.id.raw]
                val layer=source.layers.firstOrNull { it.id.raw==sourceId && it.name in eyeNames } ?: continue
                val mesh=requireNotNull(d.mesh)
                val xs=mesh.positions.filterIndexed { i,_->i%2==0 };val ys=mesh.positions.filterIndexed { i,_->i%2==1 }
                val ux=xs.min();val vy=ys.min();val uw=xs.max()-ux;val vh=ys.max()-vy
                val bounds=layer.bounds
                for(open in listOf(0f,.05f,.08f,.10f,.12f,.15f,.20f,.25f,.35f,.50f,.60f,.65f,.75f,.80f,.85f,.90f,1f)) {
                    val closed=layer.name.startsWith("eye_close")
                    val opacity=when {
                        closed->if(open<=.80f) 1f else (1-open)/.20f
                        layer.name.startsWith("eyelash")->((open-.60f)/.15f).coerceIn(0f,1f)
                        else->((open-.35f)/.15f).coerceIn(0f,1f)
                    }
                    val delta=FloatArray(mesh.positions.size)
                    if(!layer.name.startsWith("irides"))for(i in mesh.positions.indices step 2) {
                        val x=bounds.left+(mesh.positions[i]-ux)/uw*bounds.width
                        val y=bounds.top+(mesh.positions[i+1]-vy)/vh*bounds.height
                        val seam=curveY(spec.getValue("seam").jsonArray,x)
                        val scale=if(layer.name.startsWith("eyelash")) .12f+.88f*open else .06f+.94f*open
                        val ty=if(closed) y+(curveY(spec.getValue("upper").jsonArray,x)-seam)*open
                               else seam+(y-seam)*scale
                        delta[i+1]=(ty-y)/bounds.height*vh
                    }
                    keys+=RigKeyformSetEdit(RigTargetRef(RigTargetKind.ART_MESH,d.id.raw),mapOf(parameter to open),
                        if(layer.name.startsWith("irides")) null else RigKeyformGeometryEdit(positionDeltas=delta.toList()),
                        RigKeyformChannelsEdit(opacity=opacity))
                }
            }
        }
    }
    if(expressionRig==null) {
    // Preserve the separated mouth pixels and move all open-mouth parts with one common aperture.
    val mouthLayers=base.rig.puppet.drawables.filter { d -> base.rig.layerIdByDrawableId[d.id.raw] in
        source.layers.filter { it.name in mouthNames }.map { it.id.raw } }
    val aperture=mouthNames.filter { it!="mouth_close" }.map { byName.getValue(it).bounds }.let {
        Bounds(it.minOf { b->b.left.toFloat() },it.minOf { b->b.top.toFloat() },
            it.maxOf { b->(b.left+b.width).toFloat() },it.maxOf { b->(b.top+b.height).toFloat() })
    }
    for(d in mouthLayers) {
        val name=source.layers.single { it.id.raw==base.rig.layerIdByDrawableId[d.id.raw] }.name
        val mesh=requireNotNull(d.mesh)
        val bounds=base.rig.sourceBoundsByDrawableId.getValue(d.id.raw)
        val xs=mesh.positions.filterIndexed { i,_->i%2==0 };val ys=mesh.positions.filterIndexed { i,_->i%2==1 }
        val ux=xs.min();val vy=ys.min();val uw=xs.max()-ux;val vh=ys.max()-vy
        require(uw>0 && vh>0)
        for(open in listOf(0f,0.15f,0.5f,1f)) for(form in listOf(-1f,0f,1f)) {
            val opacity=if(name=="mouth_close") 1-(open/0.15f).coerceIn(0f,1f) else (open/0.15f).coerceIn(0f,1f)
            val delta=FloatArray(mesh.positions.size)
            if(name!="mouth_close") for(i in mesh.positions.indices step 2) {
                val x=bounds.left+(mesh.positions[i]-ux)/uw*bounds.width
                val y=bounds.top+(mesh.positions[i+1]-vy)/vh*bounds.height
                val tx=aperture.centerX+(x-aperture.centerX)*(1+0.06f*form)
                val ty=aperture.centerY+(y-aperture.centerY)*(0.12f+0.88f*open)
                delta[i]=(tx-x)/bounds.width*uw
                delta[i+1]=(ty-y)/bounds.height*vh
            }
            keys+=RigKeyformSetEdit(RigTargetRef(RigTargetKind.ART_MESH,d.id.raw),
                mapOf("ParamMouthOpenY" to open,"ParamMouthForm" to form),
                RigKeyformGeometryEdit(positionDeltas=delta.toList()),RigKeyformChannelsEdit(opacity=opacity))
        }
    }
    } else {
    // Mouth cavity/lips deform continuously. Teeth and tongue retain their
    // volume and are clipped by the moving cavity instead of being flattened.
    val mouthLayers=base.rig.puppet.drawables.filter { d -> base.rig.layerIdByDrawableId[d.id.raw] in
        source.layers.filter { it.name in mouthNames }.map { it.id.raw } }
    val aperture=mouthNames.filter { it!="mouth_close" }.map { byName.getValue(it).bounds }.let {
        Bounds(it.minOf { b->b.left.toFloat() },it.minOf { b->b.top.toFloat() },
            it.maxOf { b->(b.left+b.width).toFloat() },it.maxOf { b->(b.top+b.height).toFloat() })
    }
    for(d in mouthLayers) {
        val name=source.layers.single { it.id.raw==base.rig.layerIdByDrawableId[d.id.raw] }.name
        val mesh=requireNotNull(d.mesh)
        val rawBounds=byName.getValue(name).bounds
        val bounds=if(expressionRig==null) base.rig.sourceBoundsByDrawableId.getValue(d.id.raw)
            else Bounds(rawBounds.left.toFloat(),rawBounds.top.toFloat(),(rawBounds.left+rawBounds.width).toFloat(),(rawBounds.top+rawBounds.height).toFloat())
        val xs=mesh.positions.filterIndexed { i,_->i%2==0 };val ys=mesh.positions.filterIndexed { i,_->i%2==1 }
        val ux=xs.min();val vy=ys.min();val uw=xs.max()-ux;val vh=ys.max()-vy
        require(uw>0 && vh>0)
        val mouthOpenKeys=(List(33) { it/32f }+listOf(.04f,.08f,.15f,.35f,.65f,.8f)).distinct().sorted()
        val mouthSpec=expressionRig?.getValue("mouth")?.jsonObject
        val neutralLips=mouthSpec?.get("neutral_lips")?.jsonPrimitive?.booleanOrNull==true
        for(open in mouthOpenKeys) for(form in List(9) { -1f+it/4f }) {
            val opacity=when(name) {
                "mouth_close"->if(neutralLips) 0f else 1-(open/.08f).coerceIn(0f,1f)
                "lip_upper","lip_lower"->if(neutralLips) 1f else (open/.08f).coerceIn(0f,1f)
                "tooth-t"->((open-.20f)/.20f).coerceIn(0f,1f)
                "tongue"->((open-.28f)/.25f).coerceIn(0f,1f)
                else->(open/.08f).coerceIn(0f,1f)
            }
            val delta=FloatArray(mesh.positions.size)
            for(i in mesh.positions.indices step 2) {
                val x=bounds.left+(mesh.positions[i]-ux)/uw*bounds.width
                val y=bounds.top+(mesh.positions[i+1]-vy)/vh*bounds.height
                val seam=expressionRig?.getValue("mouth")?.jsonObject?.getValue("seam")?.jsonArray?.let { curveY(it,x) } ?: aperture.centerY
                val u=((x-aperture.centerX)/(aperture.width*.5f)).coerceIn(-1.2f,1.2f)
                val opening=if(neutralLips) open.toDouble().pow(.8).toFloat() else sqrt(open)
                val width=if(neutralLips || name=="mouth_close") 1f else .70f+.30f*opening
                val tx=aperture.centerX+(x-aperture.centerX)*width*(1+.16f*form)
                val cornerLift=-form*aperture.width*.065f*u*u
                val ty=when(name) {
                    "mouth_close"->y+cornerLift
                    "lip_upper"->if(neutralLips) y+(curveY(mouthSpec!!.getValue("upper").jsonArray,x)-seam)*opening+cornerLift
                                 else seam+(y-seam)*(.025f+.975f*opening)+cornerLift
                    "lip_lower"->if(neutralLips) y+(curveY(mouthSpec!!.getValue("lower").jsonArray,x)-seam)*opening+cornerLift
                                 else seam+(y-seam)*(.025f+.975f*opening)+cornerLift
                    "tooth-t"->y+(seam-aperture.top)*(1-opening)+cornerLift
                    "tongue"->y-(aperture.bottom-seam)*(1-opening)*.55f+cornerLift
                    else->seam+(y-seam)*(.025f+.975f*opening)+cornerLift
                }
                delta[i]=(tx-x)/bounds.width*uw
                delta[i+1]=(ty-y)/bounds.height*vh
            }
            keys+=RigKeyformSetEdit(RigTargetRef(RigTargetKind.ART_MESH,d.id.raw),
                mapOf("ParamMouthOpenY" to open,"ParamMouthForm" to form),
                RigKeyformGeometryEdit(positionDeltas=delta.toList()),RigKeyformChannelsEdit(opacity=opacity))
        }
    }
    }
    val params=listOf(
        RigParameterEdit("ParamBreath","呼吸",0f,1f,0f),
        RigParameterEdit("ParamBodyAngleZ","身体倾斜",-10f,10f,0f),
        RigParameterEdit("ParamArmLSwing","左手臂轻摆",-1f,1f,0f,created=true),
        RigParameterEdit("ParamArmRSwing","右手臂轻摆",-1f,1f,0f,created=true),
        RigParameterEdit("ParamSkirtSwing",garmentLabel,-1f,1f,0f,created=true))
    val old=config.rigEdits
    config=config.copy(rigEdits=old.copy(parameterEdits=old.parameterEdits.filter { it.id !in params.map { p->p.id } }+params,
        warpEdits=old.warpEdits+warps,keyformSetEdits=old.keyformSetEdits+keys))
    val report=buildJsonObject {
        put("settings",settings)
        put("keyform_edits",keys.size)
        putJsonObject("targets") {
            putJsonArray("ParamBreath") { expanded.drawables.forEach { add(it.id.raw) } }
            putJsonArray("ParamBodyAngleZ") { expanded.drawables.forEach { add(it.id.raw) } }
            for((id,names) in mapOf("ParamArmLSwing" to setOf("arm-l","hand-l"),
                "ParamArmRSwing" to setOf("arm-r","hand-r"),"ParamSkirtSwing" to garmentNames))
                putJsonArray(id) { meshIds(names).forEach { add(it) } }
        }
        putJsonArray("warp_ids") { warps.forEach { add(it.id) } }
        put("body_warp","DeformBodyZBreath")
        put("skirt_driver","motion curves; no simultaneous physics driver")
    }
    Files.writeString(output.resolve("procedural-rig.json"),Json { prettyPrint=true }.encodeToString(report))
    return config
}
