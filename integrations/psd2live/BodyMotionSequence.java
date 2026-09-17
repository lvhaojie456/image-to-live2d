// SPDX-License-Identifier: MIT
import com.live2d.sdk.cubism.core.*;
import java.nio.file.*;
import java.util.*;
import javax.imageio.ImageIO;

/** Evaluate exported MOC keyforms with official Core, then use the Kit diagnostic rasterizer. */
public class BodyMotionSequence {
    public static void main(String[] args) throws Exception {
        if(args.length!=5)throw new IllegalArgumentException("MOC TEXTURE OUTPUT TSV SIZE");
        Path mocPath=Path.of(args[0]),out=Path.of(args[2]);Files.createDirectories(out);
        render_core.main(new String[]{args[0],out.resolve("neutral.png").toString(),args[1],"--size",args[4]});
        var lines=Files.readAllLines(Path.of(args[3]));var header=lines.getFirst().split("\t");
        List<Object> poses=new ArrayList<>();boolean passed=true;double footMax=0;
        try(CubismMoc moc=CubismMoc.instantiate(Files.readAllBytes(mocPath));CubismModel model=moc.instantiateModel()) {
            render_core.drawables=model.getDrawableViews();
            validate_core.drawables=model.getDrawableViews();
            validate_core.params=model.getParameterViews();
            validate_core.ppu=model.getCanvasInfo().getPixelsPerUnit();
            validate_core.reset(model);
            var neutral=validate_core.snapshot();
            for(int index=1;index<lines.size();index++) {
                var fields=lines.get(index).split("\t");
                if(fields.length!=header.length)throw new IllegalArgumentException("Incomplete pose row");
                validate_core.reset(model);
                for(int j=2;j<fields.length;j++) {
                    var parameter=model.findParameterView(header[j]);
                    if(parameter==null)throw new IllegalArgumentException("Unknown parameter: "+header[j]);
                    float value=Float.parseFloat(fields[j]);
                    if(!Float.isFinite(value)||value<parameter.getMinimumValue()||value>parameter.getMaximumValue())throw new IllegalArgumentException("Invalid pose value");
                    parameter.setValue(value);
                }
                model.update();var pose=validate_core.evaluate(fields[0],neutral);
                passed &= Boolean.TRUE.equals(pose.get("finite"));
                passed &= ((Number)pose.get("trianglesFlippedFromNeutral")).intValue()==0;
                passed &= ((Number)pose.get("degenerateTriangles")).intValue()==0;
                for(int i=0;i<render_core.drawables.length;i++) {
                    var drawable=render_core.drawables[i];
                    if(!drawable.getId().contains("Footwear"))continue;
                    float[] actual=drawable.getVertexPositions(),base=neutral[i].xy();
                    for(int k=0;k<actual.length;k+=2)footMax=Math.max(footMax,Math.hypot(actual[k]-base[k],actual[k+1]-base[k+1])*validate_core.ppu);
                }
                if(fields[1].equals("1"))ImageIO.write(render_core.renderFrame(model),"PNG",out.resolve(fields[0]+".png").toFile());
                poses.add(pose);
            }
        }
        passed &= footMax<0.25;
        var report=validate_core.obj("passed",passed,"poseCount",poses.size(),"feetMaxDisplacementPixels",footMax,
            "scope","Official Core evaluation of emitted motion samples and body parameter combinations; Java2D diagnostic rendering, no physics simulation or camera test", "poses",poses);
        Files.writeString(out.resolve("sequence-checks.json"),validate_core.json(report,0));
        System.out.println("Evaluated "+poses.size()+" poses; feet max displacement="+footMax+"; passed="+passed);
        if(!passed)System.exit(1);
    }
}
